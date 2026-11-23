"""자동 업데이트.

1) 켤 때 깃허브에 최신 버전을 물어봄 (보내는 건 '최신 버전 알려 줘' 요청뿐, 기록·개인 정보 없음)
2) 새 버전이 있으면 화면에 안내 → 사용자가 [지금 업데이트]를 누를 때만 진행
3) zip 받기 → 파일 지문(SHA-256)·크기 확인 → 풀기 → 교체 도우미(update_helper.cmd) 실행 → 프로그램 종료
4) 도우미: 프로그램이 꺼지길 기다림 → 프로그램 파일(RaidBriefer.exe, _internal 등)만 바꿈
   → user_data(기록)·cache 는 건드리지 않음 → 실패하면 예전 파일로 되돌림 → 다시 켬

exe 로 실행할 때만 직접 바꿀 수 있음. 소스로 실행하면 안내만(받기 페이지 열기).
시험용: 환경 변수 RAIDBRIEFER_UPDATE_API 로 최신 버전 정보 주소를 바꿀 수 있음.
"""

import hashlib
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import zipfile

import requests

from app import paths
from app.version import VERSION

REPO = "Pagliatto/RaidBriefer"
API = os.environ.get("RAIDBRIEFER_UPDATE_API", f"https://api.github.com/repos/{REPO}/releases/latest")
ASSET_VERSIONED = re.compile(r"^RaidBriefer-v[\d.]+-windows\.zip$")  # 배포 파일 이름 (버전 포함)
WORK_DIR = paths.HOME_DIR / "_update"  # 받은 파일·예전 파일 백업 (끝나면 지움). 같은 드라이브라 옮기기가 빠름
HELPER = paths.BUNDLE_DIR / "app" / "update_helper.cmd"

_lock = threading.Lock()
_work_lock = threading.Lock()  # 정리와 새 다운로드의 작업 폴더 사용을 직렬화한다.
_cleanup_running = False
_cleanup_done = False
_cleanup_verified = False
_cleanup_retry_at = 0.0
_started_ok = False
_state = {"current": VERSION, "latest": None, "available": False, "page": f"https://github.com/{REPO}/releases/latest",
          "can_apply": bool(getattr(sys, "frozen", False)), "status": "idle", "error": None, "check_error": None, "check_finished": False,
          "retry": False}  # retry: 도우미가 켜지기 전 실패 → 화면에서 바로 다시 시도할 수 있음
_asset = None
# 도우미가 프로그램 파일을 하나도 바꾸기 전에 그만둔 실패 이유 (update_helper.cmd 의 REASON)
_UNTOUCHED_REASONS = ("wait-timeout", "backup-create-failed", "backup-internal-failed")
_on_exit = None  # 프로그램 창을 닫는 함수 (window.py 가 넣어 줌)


def _ver(text: str) -> tuple:
    nums = []
    for part in text.lstrip("vV").split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        nums.append(int(digits or 0))
    return tuple(nums)


def _installed_manually(started: bool, target: str) -> bool:
    """업데이트하려던 버전(target)을 사용자가 직접 설치해서 정상으로 켜졌는지.
    표식이 손상돼 '1' 처럼 남으면 판별할 수 없으므로 아니라고 본다 (백업 보존)"""
    return bool(started and re.fullmatch(r"\d+\.\d+\.\d+", target or "") and _ver(VERSION) >= _ver(target))


def set_exit_handler(fn) -> None:
    global _on_exit
    _on_exit = fn


def state() -> dict:
    with _lock:
        return dict(_state)


def _set(**kw) -> None:
    with _lock:
        _state.update(kw)


def check() -> dict:
    """깃허브에 최신 버전 묻기. 인터넷이 안 되면 조용히 넘어감"""
    global _asset
    try:
        # 데이터 받기(tarkov_api)와 같은 방식(requests)으로 → PC마다 인증서 문제 없이 똑같이 동작
        res = requests.get(API, headers={"User-Agent": f"RaidBriefer/{VERSION}", "Accept": "application/vnd.github+json"}, timeout=8)
        res.raise_for_status()
        info = res.json()
        latest = info.get("tag_name", "").lstrip("vV")
        assets = info.get("assets", [])
        asset = next((a for a in assets if ASSET_VERSIONED.match(a.get("name", ""))), None)
        _asset = asset
        _set(latest=latest, available=bool(latest) and _ver(latest) > _ver(VERSION) and asset is not None,
             page=info.get("html_url") or _state["page"])
    except Exception as error:  # 인터넷이 잠깐 안 될 수 있음 → check_in_background 가 다시 시도
        _set(check_error=f"{type(error).__name__}: {error}")
    return state()


def _check_with_retry() -> None:
    """켤 때 확인. 실패하면 30초 간격으로 몇 번 더 (인터넷이 늦게 연결되는 경우)"""
    import time

    for wait in (0, 30, 60, 120):
        time.sleep(wait)
        if check().get("latest"):
            break
    _set(check_finished=True)


def _cleanup_work(started: bool) -> bool:
    """작업 잠금을 가진 일꾼만 호출한다. 실패 표식과 미확인 백업은 보존한다."""
    global _cleanup_verified
    failed = paths.USER_DIR / "update_failed.txt"

    def marker(name):
        try:
            return (WORK_DIR / name).read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    if failed.exists():
        try:
            reason = failed.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            reason = ""
        target = marker("target_version.txt")
        # 실패 뒤 사용자가 새 버전을 직접 설치함 (지금 버전 ≥ 업데이트하려던 버전) → 실패 표시가 남을 이유 없음
        manual = _installed_manually(started, target)
        # 원래 버전으로 되돌린 것이 확인됨 → 프로그램은 온전. 이번 실행에서만 알리고 다음에는 다시 시도할 수 있게
        rolled_back = bool(started and reason == "install-failed-rollback-verified"
                           and marker("rollback.ok") == "verified")
        # 도우미가 아무것도 바꾸기 전에 그만둠 (안 꺼짐·백업 폴더 못 만듦·_internal 못 옮김) + 백업 폴더에 옮겨진 파일 없음
        # → 프로그램은 온전. 되돌림 확인과 같이 이번 실행에서만 알리고 정리
        untouched = bool(started and reason in _UNTOUCHED_REASONS
                         and not (WORK_DIR / "old" / "_internal").exists()
                         and not (WORK_DIR / "old" / "RaidBriefer.exe").exists())
        rolled_back = rolled_back or untouched
        if not manual:
            _set(status="failed", error="install")
        if not (manual or rolled_back):
            return False  # 되돌리기가 확인되지 않은 백업은 그대로 보존
        if WORK_DIR.exists():
            shutil.rmtree(WORK_DIR)
        failed.unlink(missing_ok=True)
        _cleanup_verified = True
        return True
    if not WORK_DIR.exists():
        return True

    markers = ("target_version.txt", "installed.ok", "rollback.ok")
    # v1.0.8 도우미는 표식을 쓰지 않고 old/new/도우미를 남긴다.
    legacy = (not any((WORK_DIR / name).exists() for name in markers)
              and (WORK_DIR / "old").is_dir()
              and (WORK_DIR / "new" / "RaidBriefer" / "RaidBriefer.exe").is_file()
              and (WORK_DIR / "new" / "RaidBriefer" / "_internal").is_dir()
              and (WORK_DIR / "update_helper.cmd").is_file())
    # 교체 도중 도우미가 죽어(전원 꺼짐 등) 표식이 없는데, 사용자가 새 버전을 직접 설치해서 정상으로 켜짐
    # (지금 버전 ≥ 업데이트하려던 버전) → 남은 백업 때문에 다음 업데이트가 막히지 않게 정리
    target = marker("target_version.txt")
    manual = _installed_manually(started, target)
    verified = (_cleanup_verified or marker("rollback.ok") == "verified" or manual
                or started and (legacy or marker("installed.ok") == "installed"
                                and marker("target_version.txt") == VERSION))
    if not verified or failed.exists():
        return False
    _cleanup_verified = True  # 일부 삭제 뒤 표식이 사라져도 같은 실행에서는 재시도한다.
    shutil.rmtree(WORK_DIR)
    return True


def _cleanup_background() -> None:
    global _cleanup_running, _cleanup_done, _cleanup_retry_at
    done = False
    try:
        with _work_lock:
            with _lock:
                busy = _state["status"] in ("downloading", "installing")
                started = _started_ok
            if not busy:
                done = _cleanup_work(started)
    except OSError:
        pass  # 도우미 종료 지연·백신 잠금은 다음 상태 조회에서 재시도한다.
    finally:
        with _lock:
            _cleanup_done = done
            _cleanup_running = False
            _cleanup_retry_at = time.monotonic() + 5


def cleanup(started: bool = False) -> None:
    """API에서는 성공 기동만 기록하고 단일 일꾼에 정리를 맡긴다. 디스크 접근 없음."""
    global _started_ok, _cleanup_running, _cleanup_retry_at
    with _lock:
        if started and not _started_ok:
            _started_ok = True
            _cleanup_retry_at = 0.0
        if (_cleanup_running or _cleanup_done or time.monotonic() < _cleanup_retry_at
                or _state["status"] in ("downloading", "installing")):
            return
        _cleanup_running = True
    try:
        threading.Thread(target=_cleanup_background, name="update-cleanup", daemon=True).start()
    except RuntimeError:
        with _lock:
            _cleanup_running = False
            _cleanup_retry_at = time.monotonic() + 5


def check_in_background() -> None:
    """켤 때 한 번: 흔적 정리 + 최신 버전 묻기"""
    cleanup()
    threading.Thread(target=_check_with_retry, name="update-check", daemon=True).start()


def apply() -> dict:
    """[지금 업데이트]: 뒤에서 받고 확인한 뒤 도우미를 켜고 프로그램을 닫음"""
    global _started_ok, _cleanup_done
    st = state()
    if not st["can_apply"]:
        raise RuntimeError("소스로 실행 중이라 직접 바꿀 수 없어요.")  # i18n-ok: 개발용
    if not st["available"] or _asset is None:
        raise RuntimeError("새 버전이 없어요.")  # i18n-ok: 개발용
    if st["status"] in ("downloading", "installing"):
        return st
    with _lock:
        if _state["status"] in ("downloading", "installing"):
            return dict(_state)
        # 이 API까지 정상 실행된 경우도 성공 기동으로 취급한다.
        _started_ok = True
        _cleanup_done = False
        _state.update(status="downloading", error=None, retry=False)
    threading.Thread(target=_run, name="update", daemon=True).start()
    return state()


def _run() -> None:
    with _work_lock:
        _run_locked()


def _run_locked() -> None:
    global _cleanup_verified
    helper_started = False
    try:
        # 상태 조회보다 먼저 업데이트를 눌러도 구버전 잔여물을 같은 규칙으로 정리한다.
        _cleanup_work(_started_ok)
        if (paths.USER_DIR / "update_failed.txt").exists():
            raise RuntimeError("backup-recovery-required")
        if (WORK_DIR / "old").exists():
            raise RuntimeError("backup-recovery-required")
        shutil.rmtree(WORK_DIR, ignore_errors=True)
        _cleanup_verified = False
        WORK_DIR.mkdir(parents=True)
        (paths.USER_DIR / "update_failed.txt").unlink(missing_ok=True)
        (WORK_DIR / "target_version.txt").write_text(str(_state["latest"]), encoding="utf-8")
        zip_path = WORK_DIR / "update.zip"
        # 1) 받기
        sha = hashlib.sha256()
        with requests.get(_asset["browser_download_url"], headers={"User-Agent": f"RaidBriefer/{VERSION}"}, timeout=60, stream=True) as res:
            res.raise_for_status()
            with open(zip_path, "wb") as out:
                for chunk in res.iter_content(1 << 16):
                    sha.update(chunk)
                    out.write(chunk)
        # 2) 깨지지 않았는지 확인 (깃허브가 알려 준 지문·크기와 비교)
        want = (_asset.get("digest") or "").removeprefix("sha256:").lower()
        if not want or sha.hexdigest() != want:
            raise RuntimeError("download-check")
        if _asset.get("size") and zip_path.stat().st_size != _asset["size"]:
            raise RuntimeError("download-check")
        # 3) 풀고, 프로그램이 제대로 들어 있는지 확인
        staging = WORK_DIR / "new"
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(staging)
        new_root = staging / "RaidBriefer"
        if not (new_root / "RaidBriefer.exe").exists() or not (new_root / "_internal").is_dir():
            raise RuntimeError("bad-package")
        zip_path.unlink()
        # 4) 도우미는 프로그램 밖(작업 폴더)에 복사해서 실행 (바뀌는 _internal 안에 있으면 안 됨)
        helper = WORK_DIR / "update_helper.cmd"
        shutil.copy(HELPER, helper)
        _set(status="installing")
        # cmd /c 는 따옴표가 여럿이면 맨 앞·맨 뒤 따옴표를 지워서, 경로에 띄어쓰기가 있으면 도우미가 아예 안 켜짐
        # (2026-11-21 실제 exe 시험에서 발견) → /s + 전체를 한 번 더 따옴표로 감싸 그대로 전달
        command = '""{}" {} "{}" "{}" "{}""'.format(helper, os.getpid(), new_root, paths.HOME_DIR, WORK_DIR / "old")
        subprocess.Popen(
            f"cmd.exe /d /s /c {command}",
            cwd=str(paths.HOME_DIR),  # 작업 폴더(_update)는 곧 지워지므로 프로그램 폴더에서 실행
            # 검은 창 없이, 프로그램이 꺼져도 계속 돌게
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
            close_fds=True,
        )
        helper_started = True
        # 5) 프로그램 닫기 → 도우미가 파일을 바꾸고 다시 켬
        if _on_exit:
            _on_exit()
        else:
            os._exit(0)
    except Exception as error:
        # 도우미가 켜지기 전 실패(받기·지문·풀기·폴더 만들기)는 프로그램 파일을 건드리지 않았으므로 바로 다시 시도 가능
        clean = (not helper_started and not (paths.USER_DIR / "update_failed.txt").exists()
                 and not (WORK_DIR / "old").exists() and not (WORK_DIR / "installed.ok").exists())
        _set(status="failed", error=str(error), retry=clean)
        if clean:
            shutil.rmtree(WORK_DIR, ignore_errors=True)

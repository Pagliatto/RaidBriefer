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
_state = {"current": VERSION, "latest": None, "available": False, "page": f"https://github.com/{REPO}/releases/latest",
          "can_apply": bool(getattr(sys, "frozen", False)), "status": "idle", "error": None, "check_error": None}
_asset = None
_on_exit = None  # 프로그램 창을 닫는 함수 (window.py 가 넣어 줌)


def _ver(text: str) -> tuple:
    nums = []
    for part in text.lstrip("vV").split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        nums.append(int(digits or 0))
    return tuple(nums)


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
            return


def cleanup() -> None:
    """지난 업데이트의 흔적 정리 (작업 폴더 지우기, 실패 표시 읽기). 여러 번 불러도 됨"""
    failed = paths.USER_DIR / "update_failed.txt"
    if failed.exists():  # 도우미가 실패해서 예전 버전으로 되돌린 경우
        _set(status="failed", error="install")
        failed.unlink(missing_ok=True)
    if WORK_DIR.exists() and _state["status"] not in ("downloading", "installing"):
        shutil.rmtree(WORK_DIR, ignore_errors=True)


def check_in_background() -> None:
    """켤 때 한 번: 흔적 정리 + 최신 버전 묻기"""
    cleanup()
    threading.Thread(target=_check_with_retry, name="update-check", daemon=True).start()


def apply() -> dict:
    """[지금 업데이트]: 뒤에서 받고 확인한 뒤 도우미를 켜고 프로그램을 닫음"""
    st = state()
    if not st["can_apply"]:
        raise RuntimeError("소스로 실행 중이라 직접 바꿀 수 없어요.")  # i18n-ok: 개발용
    if not st["available"] or _asset is None:
        raise RuntimeError("새 버전이 없어요.")  # i18n-ok: 개발용
    if st["status"] in ("downloading", "installing"):
        return st
    _set(status="downloading", error=None)
    threading.Thread(target=_run, name="update", daemon=True).start()
    return state()


def _run() -> None:
    try:
        shutil.rmtree(WORK_DIR, ignore_errors=True)
        WORK_DIR.mkdir(parents=True)
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
        subprocess.Popen(
            ["cmd.exe", "/c", str(helper), str(os.getpid()), str(new_root), str(paths.HOME_DIR), str(WORK_DIR / "old")],
            cwd=str(paths.HOME_DIR),  # 작업 폴더(_update)는 곧 지워지므로 프로그램 폴더에서 실행
            # 검은 창 없이, 프로그램이 꺼져도 계속 돌게
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
            close_fds=True,
        )
        # 5) 프로그램 닫기 → 도우미가 파일을 바꾸고 다시 켬
        if _on_exit:
            _on_exit()
        else:
            os._exit(0)
    except Exception as error:
        _set(status="failed", error=str(error))
        shutil.rmtree(WORK_DIR, ignore_errors=True)

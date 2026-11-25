"""게임 스크린샷 정리 (2026-11-25 사용자 결정): 설정 창에서 직접 누를 때만.

내 위치 표시에 쓰려고 찍은 스크린샷이 쌓여서(한 장 약 8MB) 치우는 방법을 준다.
- 대상: 스크린샷 폴더 바로 안의, 게임이 붙인 이름 모양(shot_watch.parse_name)의 .png 만. 다른 파일·하위 폴더·연결(링크)은 안 건드림
- 방법: 휴지통으로만. 휴지통에 넣을 수 없는 곳(네트워크·이동식 드라이브, 휴지통을 끈 PC)이나
  휴지통 용량보다 큰 파일은 지우지 않음 (Windows 가 완전히 지워 버리는 경우를 미리 걸러 냄)
- 방금 찍은 것(KEEP_RECENT 초 안)은 남김: 레이드 중 내 위치 표시가 사라지지 않게, 게임이 아직 쓰는 중인 파일을 건드리지 않게
- 사용자가 본 개수 뒤에 새로 찍힌 것은 안 지움 (as_of)
- 자동으로는 절대 지우지 않음
"""

import ctypes
import os
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path

from app import shot_watch

KEEP_RECENT = 60      # 초. shot_watch.SHOW_SECONDS(30) 보다 넉넉하게
BATCH = 100           # 한 번에 휴지통으로 넘기는 파일 수
MAX_PATH_SAFE = 259   # 이보다 긴 경로는 옛 셸 함수가 못 다뤄서 건너뜀

_lock = threading.Lock()  # 정리는 한 번에 하나만


def folders() -> list[Path]:
    """실제로 있는 스크린샷 폴더 (같은 폴더를 가리키는 후보는 하나로)"""
    seen, out = set(), []
    for folder in shot_watch.candidates():
        try:
            if not folder.is_dir():
                continue
            key = os.path.normcase(os.path.realpath(folder))
        except OSError:
            continue
        if key not in seen:
            seen.add(key)
            out.append(folder)
    return out


def _shots(folder: Path, not_after: float) -> list[tuple[str, int]]:
    """폴더 바로 안의 게임 스크린샷 [(전체 경로, 크기)]. 수정 시각이 not_after 뒤인 것은 뺌"""
    found = []
    try:
        with os.scandir(folder) as entries:
            for entry in entries:
                if not shot_watch.parse_name(entry.name):
                    continue
                try:
                    if entry.is_symlink() or not entry.is_file(follow_symlinks=False):
                        continue
                    info = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                if info.st_mtime <= not_after:
                    found.append((entry.path, info.st_size))
    except OSError:
        return []
    return found


def scan(folder_list: list[Path] | None = None, now: float | None = None) -> dict:
    """지금 치울 수 있는 스크린샷 개수·용량. 파일은 열지 않음"""
    now = time.time() if now is None else now
    folder_list = folders() if folder_list is None else folder_list
    as_of = now - KEEP_RECENT
    shots = [shot for folder in folder_list for shot in _shots(folder, as_of)]
    return {"found": bool(folder_list), "count": len(shots), "bytes": sum(size for _, size in shots), "as_of": as_of}


def _recycle_limit(path: str) -> int:
    """이 경로에서 휴지통에 넣을 수 있는 파일 한 개의 최대 크기(바이트). 0 = 휴지통을 쓸 수 없음.
    휴지통 용량보다 큰 파일은 Windows 가 완전히 지워 버리므로 크기도 함께 본다. 확실하지 않으면 0 (지우지 않는 쪽)"""
    if sys.platform != "win32":
        return 0
    try:
        import shutil
        import winreg

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        root = ctypes.create_unicode_buffer(1024)
        kernel32.GetVolumePathNameW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
        kernel32.GetVolumePathNameW.restype = wintypes.BOOL
        if not kernel32.GetVolumePathNameW(path, root, len(root)):
            return 0
        kernel32.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
        kernel32.GetDriveTypeW.restype = wintypes.UINT
        if kernel32.GetDriveTypeW(root.value) != 3:  # DRIVE_FIXED 만 (네트워크·이동식·램 드라이브는 휴지통이 없음)
            return 0

        def reg_value(hive, key, name):
            """값이 없으면 None (기본값을 쓰는 PC). 권한 등으로 못 읽으면 예외 → 바깥에서 0"""
            try:
                with winreg.OpenKey(hive, key) as handle:
                    return winreg.QueryValueEx(handle, name)[0]
            except FileNotFoundError:
                return None

        # 'Windows 정책: 휴지통을 쓰지 않고 바로 삭제'
        policy = r"Software\Microsoft\Windows\CurrentVersion\Policies\Explorer"
        if reg_value(winreg.HKEY_CURRENT_USER, policy, "NoRecycleFiles") or reg_value(
                winreg.HKEY_LOCAL_MACHINE, policy, "NoRecycleFiles"):
            return 0
        volume = ctypes.create_unicode_buffer(64)
        kernel32.GetVolumeNameForVolumeMountPointW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
        kernel32.GetVolumeNameForVolumeMountPointW.restype = wintypes.BOOL
        if not kernel32.GetVolumeNameForVolumeMountPointW(root.value, volume, len(volume)):
            return 0
        guid = volume.value[volume.value.find("{"):volume.value.find("}") + 1]
        if not guid.startswith("{") or not guid.endswith("}"):
            return 0
        # 휴지통 속성에서 이 드라이브를 '휴지통에 넣지 않고 바로 삭제' 로 둔 경우, 그리고 휴지통 최대 크기(MB)
        bucket = r"Software\Microsoft\Windows\CurrentVersion\Explorer\BitBucket\Volume" + "\\" + guid
        if reg_value(winreg.HKEY_CURRENT_USER, bucket, "NukeOnDelete"):
            return 0
        capacity = reg_value(winreg.HKEY_CURRENT_USER, bucket, "MaxCapacity")
        if capacity is None:
            limit = shutil.disk_usage(root.value).total // 20  # 설정한 적 없는 PC: Windows 기본값(드라이브의 5% 이상)보다 작게 잡음
        elif type(capacity) is int and capacity > 0:
            limit = capacity * 1024 * 1024
        else:
            return 0
        return limit // 2  # 여유 있게 절반까지만
    except Exception:
        return 0


class _FileOp(ctypes.Structure):
    _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", ctypes.c_void_p),
                ("pTo", ctypes.c_void_p), ("fFlags", wintypes.WORD), ("fAnyOperationsAborted", wintypes.BOOL),
                ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]


def _recycle(paths: list[str]) -> None:
    """파일들을 휴지통으로. 결과는 부른 쪽이 파일이 남았는지로 확인한다"""
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    shell32.SHFileOperationW.argtypes = [ctypes.POINTER(_FileOp)]
    shell32.SHFileOperationW.restype = ctypes.c_int
    # 경로들을 \0 으로 잇고 끝에 \0 하나 더 (버퍼가 마지막 \0 을 하나 더 붙임)
    source = ctypes.create_unicode_buffer("\0".join(paths) + "\0")
    # FO_DELETE(3) + 되살릴 수 있게(ALLOWUNDO 0x40), 창·물음 없이(SILENT 0x4, NOCONFIRMATION 0x10, NOERRORUI 0x400),
    # 폴더는 대상 아님(FILESONLY 0x80). 완전 삭제가 되는 경우는 _recycle_limit 이 미리 걸러 냄.
    # WANTNUKEWARNING(0x4000)은 그래도 남은 경우의 마지막 장치: 말없이 지우지 않고 Windows 가 먼저 물음
    operation = _FileOp(None, 3, ctypes.cast(source, ctypes.c_void_p), None,
                        0x40 | 0x4 | 0x10 | 0x400 | 0x80 | 0x4000, False, None, None)
    shell32.SHFileOperationW(ctypes.byref(operation))


def clean(as_of: float, folder_list: list[Path] | None = None, now: float | None = None,
          recycle_limit=_recycle_limit, recycle=_recycle) -> dict:
    """as_of 까지의 게임 스크린샷을 휴지통으로. {"ok", "moved", "bytes", "left", "reason"}
    left = 대상인데 남은 파일 수 (휴지통 불가·너무 큼·경로가 너무 김·다른 프로그램이 쓰는 중)
    reason: busy(이미 정리 중) / no_recycle(휴지통을 쓸 수 없어 그대로 둔 것이 있음)"""
    now = time.time() if now is None else now
    # 화면이 본 시각보다 뒤로는 안 감, 방금 찍은 것도 남김
    as_of = min(float(as_of), now - KEEP_RECENT)
    if not _lock.acquire(blocking=False):
        return {"ok": False, "moved": 0, "bytes": 0, "left": 0, "reason": "busy"}
    try:
        moved = freed = left = 0
        reason = None
        for folder in (folders() if folder_list is None else folder_list):
            found = _shots(folder, as_of)
            if not found:
                continue
            limit = recycle_limit(str(folder))
            if limit <= 0:
                left += len(found)
                reason = "no_recycle"
                continue
            # 휴지통 용량보다 큰 파일(완전히 지워짐)·경로가 너무 긴 파일(옛 셸 함수가 못 다룸)은 그대로 둠
            shots = [(path, size) for path, size in found if size <= limit and len(path) <= MAX_PATH_SAFE]
            if len(shots) < len(found):
                left += len(found) - len(shots)
                if any(size > limit for _, size in found):
                    reason = "no_recycle"
            for start in range(0, len(shots), BATCH):
                batch = shots[start:start + BATCH]
                try:
                    recycle([path for path, _ in batch])
                except Exception:
                    pass  # 아래에서 남은 파일로 셈
                for path, size in batch:
                    if os.path.lexists(path):
                        left += 1
                    else:
                        moved += 1
                        freed += size
        return {"ok": left == 0, "moved": moved, "bytes": freed, "left": left, "reason": reason}
    finally:
        _lock.release()


def open_folder(folder_list: list[Path] | None = None) -> bool:
    """탐색기로 스크린샷 폴더를 연다 (남길 사진을 직접 옮길 수 있게). 하나도 못 열면 False.
    정리 대상 폴더가 여럿이면(문서 폴더를 원드라이브로 옮긴 PC 등) 모두 연다 — 안 열린 폴더의 사진이 모르게 정리되지 않게"""
    opened = False
    for folder in (folders() if folder_list is None else folder_list):
        try:
            os.startfile(folder)  # type: ignore[attr-defined]
            opened = True
        except Exception:
            pass
    return opened

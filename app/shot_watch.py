"""게임 스크린샷의 '파일 이름'에서 내 위치를 읽는 파일 (2026-11-24 사용자 결정).

게임이 스크린샷을 찍으면 파일 이름에 위치·방향이 들어간다 (1.3.5 확인):
  2026-11-24[22-36]_32.47, -1.08, 39.41_-0.09715, 0.71947, 0.10280, 0.67997_15.68 (0).png
  날짜[시-분]_x, 높이, z_방향 4개(x, y, z, w)_뜻 모르는 값 (번호)

- 폴더의 파일 이름·수정 시각만 본다. 그림 파일은 열지 않고, 지우거나 옮기지도 않는다
- 스크린샷 키는 사용자가 직접 누른다 (프로그램이 대신 누르면 매크로라 만들지 않음)
- 레이드 중일 때만 폴더를 보고, 이번 레이드가 시작된 뒤에 찍힌 것만 쓴다
"""

import ctypes
import math
import os
import re
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path

from app import game_state

_NUM = r"(-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)"
NAME_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}\[\d{2}-\d{2}\]_" + ", ".join([_NUM] * 3) + "_" + ", ".join([_NUM] * 4) + r"_")
SHOT_DIR = ("Escape from Tarkov", "Screenshots")
SHOW_SECONDS = 30   # 화면에 보여 주는 시간 (map.js ME_SHOW_MS 와 같게). 이보다 오래된 건 내보내지 않음
START_SLACK = 2     # 로그 시각은 초 단위로 잘려 있어서 조금 여유
COORD_LIMIT = 100000  # 이보다 큰 좌표는 잘못된 이름으로 봄

_lock = threading.Lock()
_latest = None   # {"id", "x", "y", "z", "fx", "fz", "at", "map_id"}
_started = False
_found = {"at": 0.0, "folders": []}  # 폴더 후보는 30초마다 다시 찾음 (문서 폴더를 옮기거나 폴더가 새로 생겨도 따라감)
FIND_EVERY = 30


def parse_name(name: str) -> dict | None:
    """파일 이름 → 위치(x, 높이 y, z)와 바라보는 방향(fx, fz). 모양이 다르면 None"""
    if not name.lower().endswith(".png"):
        return None
    match = NAME_RE.match(name)
    if not match:
        return None
    try:
        x, y, z, qx, qy, qz, qw = (float(value) for value in match.groups())
    except ValueError:
        return None
    if not all(math.isfinite(v) for v in (x, y, z, qx, qy, qz, qw)) or max(abs(x), abs(y), abs(z)) > COORD_LIMIT:
        return None
    # 방향 4개(쿼터니언)로 '앞쪽' 화살표를 구함: 앞(0,0,1)을 돌린 것의 가로 성분
    fx = 2 * (qx * qz + qw * qy)
    fz = 1 - 2 * (qx * qx + qy * qy)
    length = math.hypot(fx, fz)
    if length < 0.05:  # 똑바로 위·아래를 봄 → 방향 없음
        fx = fz = 0.0
    else:
        fx, fz = fx / length, fz / length
    return {"x": x, "y": y, "z": z, "fx": round(fx, 4), "fz": round(fz, 4)}


def _documents() -> Path | None:
    """Windows '문서' 폴더 (원드라이브로 옮긴 PC 도 실제 위치를 돌려줌)"""
    if sys.platform != "win32":
        return None
    try:
        class GUID(ctypes.Structure):
            _fields_ = [("a", wintypes.DWORD), ("b", wintypes.WORD), ("c", wintypes.WORD), ("d", ctypes.c_ubyte * 8)]

        # FOLDERID_Documents {FDD39AD0-238F-46AF-ADB4-6C85480369C7}
        folder_id = GUID(0xFDD39AD0, 0x238F, 0x46AF, (ctypes.c_ubyte * 8)(0xAD, 0xB4, 0x6C, 0x85, 0x48, 0x03, 0x69, 0xC7))
        shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        ole32 = ctypes.WinDLL("ole32", use_last_error=True)
        shell32.SHGetKnownFolderPath.argtypes = [ctypes.POINTER(GUID), wintypes.DWORD, wintypes.HANDLE,
                                                 ctypes.POINTER(ctypes.c_wchar_p)]
        shell32.SHGetKnownFolderPath.restype = ctypes.c_long
        ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
        ole32.CoTaskMemFree.restype = None
        out = ctypes.c_wchar_p()
        if shell32.SHGetKnownFolderPath(ctypes.byref(folder_id), 0, None, ctypes.byref(out)) != 0:
            return None
        try:
            return Path(out.value) if out.value else None
        finally:
            ole32.CoTaskMemFree(out)
    except Exception:
        return None


def candidates() -> list[Path]:
    """스크린샷 폴더 후보 (있는 것만 씀). 문서 폴더 위치는 PC 마다 다름"""
    roots = [_documents(), Path.home() / "Documents"]
    one_drive = os.environ.get("OneDrive")
    if one_drive:
        roots.append(Path(one_drive) / "Documents")
    seen, out = set(), []
    for root in roots:
        if root is None:
            continue
        folder = root.joinpath(*SHOT_DIR)
        key = os.path.normcase(str(folder))
        if key not in seen:
            seen.add(key)
            out.append(folder)
    return out


def newest_shot(folder: Path, not_before: float, not_after: float) -> dict | None:
    """폴더에서 [not_before, not_after] 사이에 찍힌, 좌표가 든 가장 새 스크린샷. 파일은 열지 않음"""
    best = None
    try:
        with os.scandir(folder) as entries:
            for entry in entries:
                parsed = parse_name(entry.name)
                if not parsed:
                    continue
                try:
                    if not entry.is_file():
                        continue
                    taken = entry.stat().st_mtime
                except OSError:
                    continue
                if not_before <= taken <= not_after and (best is None or taken > best["at"]):
                    best = {**parsed, "at": taken, "id": f"{entry.name}|{int(taken * 1000)}"}
    except OSError:
        return None
    return best


def _tick(folders=None, now=None) -> None:
    """레이드 중일 때만 폴더를 한 번 본다 (시험에서는 folders·now 를 넣어 부름)"""
    global _latest
    state = game_state.get()
    if state.get("status") != "raid" or not state.get("since"):
        with _lock:
            _latest = None
        return
    now = time.time() if now is None else now
    # 이번 레이드가 시작된 뒤, 그리고 아직 화면에 보여 줄 시간 안의 것만 (PC 시계가 뒤로 가도 옛 사진이 뜨지 않게)
    not_before = max(state["since"] / 1000 - START_SLACK, now - SHOW_SECONDS)
    if folders is None:
        if time.monotonic() - _found["at"] >= FIND_EVERY or not _found["folders"]:
            _found.update(at=time.monotonic(), folders=candidates())
        folders = _found["folders"]  # 없는 폴더는 newest_shot 이 조용히 넘어감
    best = None
    for folder in folders:
        shot = newest_shot(folder, not_before, now + START_SLACK)
        if shot and (best is None or shot["at"] > best["at"]):
            best = shot
    with _lock:
        if best is None:
            # 지난 위치가 이번 레이드 것이고 아직 보여 줄 시간이면 그대로 둠 (폴더를 잠깐 못 읽어도)
            if _latest and not (not_before <= _latest["at"] / 1000 <= now + START_SLACK
                                and _latest["map_id"] == state.get("map_id") and _latest["since"] == state["since"]):
                _latest = None
            return
        _latest = {**best, "at": int(best["at"] * 1000), "map_id": state.get("map_id"), "since": state["since"]}


def _run() -> None:
    while True:
        try:
            _tick()
        except Exception:
            pass  # 폴더 문제로 다른 기능이 멈추지 않게. 다음 회차에 다시
        time.sleep(1)


def start() -> None:
    """서버 시작 때 한 번"""
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=_run, name="shot-watch", daemon=True).start()


def latest() -> dict | None:
    """화면(/api/state)에 줄 가장 새 내 위치. 없으면 None. 파일 이름·경로는 내보내지 않음"""
    with _lock:
        if not _latest:
            return None
        out = dict(_latest)
    # 읽는 사이에 레이드가 끝났거나 바뀌었으면 내보내지 않음 (지운 표시가 다시 뜨지 않게)
    state = game_state.get()
    if (state.get("status") != "raid" or state.get("since") != out.pop("since") or state.get("map_id") != out["map_id"]
            or time.time() - out["at"] / 1000 > SHOW_SECONDS):
        return None
    out["id"] = str(abs(hash(out["id"])))  # 파일 이름은 감추고, 바뀌었는지만 알 수 있게
    return out

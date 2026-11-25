"""사용자 설정 (오른쪽 위 ⚙ 버튼에서 바꿈).

- 저장 위치: user_data/settings.json  (사람마다 다르니 Git에는 올리지 않음)
- game_dir : 타르코프 설치 폴더. 그 안의 Logs 폴더를 읽는다.
"""

import json
import os
import hashlib
import time
import string
import threading
from pathlib import Path

from app import cleanup, i18n, paths

USER_DIR = paths.USER_DIR
SETTINGS_FILE = USER_DIR / "settings.json"
DEFAULT_GAME_DIR = r"C:\Battlestate Games\Escape from Tarkov"
# setup_done: 처음 실행 때 뜨는 '처음 설정' 화면을 끝냈는지
# edition: 게임 에디션 → 은신처 시작 레벨이 달라짐 (창고, 광신도 제단). 위키 기준
# hideout_all: 은신처 재료 합계에 조건이 남은 시설(지금 못 짓는 시설)까지 합칠지 (기본: 지을 수 있는 시설만)
DEFAULTS = {"game_dir": DEFAULT_GAME_DIR, "setup_done": False, "edition": "standard", "hideout_all": False, "lang": "",
            "last_seen_version": "", "brightness_mode": "off", "brightness_strength": 35, "brightness_monitor": "",
            "raid_border": "on"}  # lang 비어 있음 = 윈도우 언어를 따름
EDITIONS = ("standard", "left_behind", "prepare_for_escape", "edge_of_darkness", "unheard")
# raid_border: 남은 시간 10분 경고 테두리 (지도 둘레 빨간 빛). 끔 / 켬(기본)
RAID_BORDERS = ("off", "on")
# 개발 중에 쓰던 옛 값 (얇게 / 보통) → 켬으로 읽음. 설정 파일을 '잘못된 파일'로 보지 않게 받아 줌
OLD_RAID_BORDERS = ("thin", "normal")

_lock = threading.Lock()
_cache: dict | None = None


class DataReadError(Exception):
    def __str__(self):
        return i18n.t("server.savedDataFailed", lang=i18n.system_lang())


_broken_copies = set()
_failed_reads = set()  # 읽지 못한 저장 파일 (알림 SAVE-01). 다시 잘 읽히면 빠짐


def saved_read_failed() -> bool:
    return bool(_failed_reads)


class SaveWriteError(OSError):
    """기록·설정 파일을 저장하지 못함 (폴더 권한·읽기 전용 등). 예상한 오류라
    ERR 기록(report.txt)에는 남기지 않고 알림함 SAVE-02 로만 알린다. OSError 를 이어받으므로 기존 except OSError 도 그대로 받음."""
    def __str__(self):
        return i18n.t("server.savedWriteFailed", lang=i18n.system_lang())


_failed_writes = set()  # 저장하지 못한 파일 (알림 SAVE-02). 그 파일 저장이 다시 성공하면 빠짐


def saved_write_failed() -> bool:
    return bool(_failed_writes)


def write_saved(path: Path, data: dict) -> None:
    """임시 파일에 쓰고 바꿔치기. 백신 등의 순간 잠금은 0.2초 × 5번 다시 시도한다.
    그래도 실패하면(폴더 권한·읽기 전용 파일 등) 임시 파일을 지우고 SAVE-02 를 켠 뒤 오류를 그대로 올린다."""
    temp = path.with_suffix(".tmp")
    try:
        path.parent.mkdir(exist_ok=True)
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        for attempt in range(5):
            try:
                temp.replace(path)
                break
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.2)
    except OSError as error:
        _failed_writes.add(path)
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass  # 지우지 못한 임시 파일은 다음 저장 때 덮어쓴다.
        raise SaveWriteError() from error
    _failed_writes.discard(path)
    _failed_reads.discard(path)  # 새로 쓴 파일은 읽을 수 있는 상태 (전체 초기화로 손상 파일을 바꾼 경우 SAVE-01 끔)


def read_saved(path: Path, validate) -> dict:
    """없는 파일만 새 기록으로 취급한다. 손상 원본은 보존하고 쓰기를 막는다."""
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        _failed_reads.discard(path)
        return {}
    except OSError as error:
        _failed_reads.add(path)
        raise DataReadError() from error
    try:
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict) or not validate(data):
            raise ValueError("saved-schema")
        _failed_reads.discard(path)
        return data
    except (ValueError, UnicodeError) as error:
        _failed_reads.add(path)
        fingerprint = (path, hashlib.sha256(raw).hexdigest())
        if fingerprint not in _broken_copies:
            backup = path.with_name(f"{path.stem}.broken-{time.time_ns()}.json")
            try:
                with backup.open("xb") as fh:
                    fh.write(raw)
                _broken_copies.add(fingerprint)
                cleanup.prune_broken(path.stem, path.parent, keep=backup)  # 손상이 계속돼도 백업은 최근 3개만
            except OSError:
                pass  # 복사도 실패하면 원본을 그대로 두고 저장을 거절한다.
        raise DataReadError() from error


def read_for_reset(path: Path, validate) -> dict | None:
    """전체 초기화 전용 읽기. 손상(내용을 해석할 수 없음)이고 broken 백업이 이미 있으면 None
    → 새로 써도 됨. 잠김 등 읽기 자체가 안 되거나 백업을 못 만든 손상 파일은 지금처럼 거절한다."""
    try:
        return read_saved(path, validate)
    except DataReadError as error:
        if not isinstance(error.__cause__, (ValueError, UnicodeError)):
            raise
        try:
            raw = path.read_bytes()
        except OSError:
            raise error
        # 기억만 믿지 않고 같은 내용의 백업이 실제로 남아 있는지 확인한다 (정리·사용자가 지웠을 수 있음).
        # 없으면 한 번 다시 만들어 보고, 그래도 없으면 덮어쓰지 않는다.
        if not cleanup.has_backup(path, raw):
            _broken_copies.discard((path, hashlib.sha256(raw).hexdigest()))
            try:
                read_saved(path, validate)
            except DataReadError:
                pass
            if not cleanup.has_backup(path, raw):
                raise error
        return None


def _valid_saved(data: dict) -> bool:
    for key, default in DEFAULTS.items():
        if key in data and type(data[key]) is not type(default):
            return False
    return (data.get("edition", "standard") in EDITIONS
            and data.get("lang", "") in ("", *i18n.LANGS)
            and data.get("brightness_mode", "off") in ("off", "raid", "always")
            and data.get("raid_border", "on") in (*RAID_BORDERS, *OLD_RAID_BORDERS)
            and 0 <= data.get("brightness_strength", 35) <= 60)


def load() -> dict:
    global _cache
    with _lock:
        # 저장할 때도 디스크를 다시 확인해 이전 캐시로 손상 파일을 덮지 않는다.
        saved = read_saved(SETTINGS_FILE, _valid_saved)
        _cache = {**DEFAULTS, **saved}
        if _cache["raid_border"] in OLD_RAID_BORDERS:
            _cache["raid_border"] = "on"
        return dict(_cache)


def save(changes: dict) -> dict:
    global _cache
    load()
    updates = {k: v for k, v in changes.items() if k in DEFAULTS}
    if "last_seen_version" in updates:
        from app.version import VERSION

        if updates["last_seen_version"] not in ("", VERSION):
            updates.pop("last_seen_version")
    if "brightness_mode" in updates and updates["brightness_mode"] not in ("off", "raid", "always"):
        updates.pop("brightness_mode")
    if "raid_border" in updates and updates["raid_border"] not in RAID_BORDERS:
        updates.pop("raid_border")
    if "brightness_strength" in updates:
        strength = updates["brightness_strength"]
        if type(strength) is not int or not 0 <= strength <= 60:
            updates.pop("brightness_strength")
    if "brightness_monitor" in updates:
        from app import brightness  # 늦게 불러와 순환 import와 잠금 순서 충돌을 피함

        monitor = updates["brightness_monitor"]
        if not isinstance(monitor, str) or (monitor and monitor not in {m["id"] for m in brightness.monitors()}):
            updates.pop("brightness_monitor")
    with _lock:
        saved = read_saved(SETTINGS_FILE, _valid_saved)
        current = {**DEFAULTS, **saved, **updates}
        if not _valid_saved(current):
            raise ValueError("settings-schema")
        write_saved(SETTINGS_FILE, current)
        _cache = current
    return dict(current)


def can_reset() -> None:
    """전체 초기화 전에 확인: 설정 파일을 덮어도 되는지 (안 되면 DataReadError). 아무것도 쓰지 않는다."""
    with _lock:
        read_for_reset(SETTINGS_FILE, _valid_saved)


def reset() -> dict:
    """전체 초기화: 설정을 처음 상태로, 게임 설치 위치만 남김.
    손상된 설정 파일은 broken 백업을 남긴 뒤 기본값으로 새로 씀 (이때 설치 위치는 기본값, 처음 설정에서 다시 고름)."""
    global _cache
    with _lock:
        saved = read_for_reset(SETTINGS_FILE, _valid_saved)
        game_dir = saved.get("game_dir", DEFAULT_GAME_DIR) if saved is not None else DEFAULT_GAME_DIR
        current = {**DEFAULTS, "game_dir": game_dir}
        write_saved(SETTINGS_FILE, current)
        _cache = current
    return dict(current)


def logs_dir() -> Path:
    """게임 로그 폴더 = 설치 폴더 안의 Logs"""
    return Path(load()["game_dir"]) / "Logs"


def check_game_dir(game_dir: str) -> dict:
    """설치 폴더가 맞는지 확인: Logs 폴더와 로그 폴더 개수"""
    logs = Path(game_dir) / "Logs"
    if not logs.is_dir():
        return {"ok": False, "message": i18n.t("server.noLogsFolder")}
    try:
        with os.scandir(logs) as entries:
            count = sum(1 for entry in entries if entry.name.lower().startswith("log_") and entry.is_dir())
    except OSError:
        count = 0
    if not count:
        return {"ok": False, "message": i18n.t("server.noLogs")}
    return {"ok": True, "message": i18n.t("server.logsFound", n=count)}


def find_game_dir() -> str | None:
    """모든 드라이브(C:, D:, E: …)에서 흔한 설치 위치를 찾아봄"""
    for letter in string.ascii_uppercase:
        candidate = Path(f"{letter}:\\") / "Battlestate Games" / "Escape from Tarkov"
        try:
            if (candidate / "Logs").is_dir():
                return str(candidate)
        except OSError:  # 빈 CD 드라이브 등
            continue
    return None

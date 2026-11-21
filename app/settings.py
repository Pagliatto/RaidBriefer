"""사용자 설정 (오른쪽 위 ⚙ 버튼에서 바꿈).

- 저장 위치: user_data/settings.json  (사람마다 다르니 Git에는 올리지 않음)
- game_dir : 타르코프 설치 폴더. 그 안의 Logs 폴더를 읽는다.
"""

import json
import hashlib
import time
import string
import threading
from pathlib import Path

from app import i18n, paths

USER_DIR = paths.USER_DIR
SETTINGS_FILE = USER_DIR / "settings.json"
DEFAULT_GAME_DIR = r"C:\Battlestate Games\Escape from Tarkov"
# setup_done: 처음 실행 때 뜨는 '처음 설정' 화면을 끝냈는지
# edition: 게임 에디션 → 은신처 시작 레벨이 달라짐 (창고, 광신도 제단). 위키 기준
# hideout_all: 은신처 재료 합계에 조건이 남은 시설(지금 못 짓는 시설)까지 합칠지 (기본: 지을 수 있는 시설만)
DEFAULTS = {"game_dir": DEFAULT_GAME_DIR, "setup_done": False, "edition": "standard", "hideout_all": False, "lang": "",
            "last_seen_version": "", "brightness_mode": "off", "brightness_strength": 35, "brightness_monitor": ""}  # lang 비어 있음 = 윈도우 언어를 따름
EDITIONS = ("standard", "left_behind", "prepare_for_escape", "edge_of_darkness", "unheard")

_lock = threading.Lock()
_cache: dict | None = None


class DataReadError(Exception):
    def __str__(self):
        return i18n.t("server.savedDataFailed", lang=i18n.system_lang())


_broken_copies = set()


def read_saved(path: Path, validate) -> dict:
    """없는 파일만 새 기록으로 취급한다. 손상 원본은 보존하고 쓰기를 막는다."""
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return {}
    except OSError as error:
        raise DataReadError() from error
    try:
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict) or not validate(data):
            raise ValueError("saved-schema")
        return data
    except (ValueError, UnicodeError) as error:
        fingerprint = (path, hashlib.sha256(raw).hexdigest())
        if fingerprint not in _broken_copies:
            backup = path.with_name(f"{path.stem}.broken-{time.time_ns()}.json")
            try:
                with backup.open("xb") as fh:
                    fh.write(raw)
                _broken_copies.add(fingerprint)
            except OSError:
                pass  # 복사도 실패하면 원본을 그대로 두고 저장을 거절한다.
        raise DataReadError() from error


def _valid_saved(data: dict) -> bool:
    for key, default in DEFAULTS.items():
        if key in data and type(data[key]) is not type(default):
            return False
    return (data.get("edition", "standard") in EDITIONS
            and data.get("lang", "") in ("", *i18n.LANGS)
            and data.get("brightness_mode", "off") in ("off", "raid", "always")
            and 0 <= data.get("brightness_strength", 35) <= 60)


def load() -> dict:
    global _cache
    with _lock:
        # 저장할 때도 디스크를 다시 확인해 이전 캐시로 손상 파일을 덮지 않는다.
        saved = read_saved(SETTINGS_FILE, _valid_saved)
        _cache = {**DEFAULTS, **saved}
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
        USER_DIR.mkdir(exist_ok=True)
        temp = SETTINGS_FILE.with_suffix(".tmp")
        temp.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(SETTINGS_FILE)
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
    count = sum(1 for f in logs.glob("log_*") if f.is_dir())
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

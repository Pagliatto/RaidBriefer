"""tarkov.dev의 전체 데이터 파일(json.tarkov.dev)을 받아서 저장해 두는 파일.

동작 방식
1. 저장해 둔 파일(cache/)이 있으면 그걸 바로 쓴다 → 프로그램이 빨리 켜짐
2. 한 시간(CHECK_HOURS)마다 "바뀐 게 있나요?"만 물어본다 (ETag 확인)
   - 안 바뀌었으면 서버가 "그대로예요(304)"라고만 답함 → 거의 0바이트
   - 바뀐 파일만 새로 받는다
3. 확인에 실패하면 저장본을 계속 쓰고, 언제 정보인지 안내한다
4. 실패한 뒤에는 바로 또 시도하지 않고 점점 오래 기다린다 (서버 배려)

저장 파일
- cache/pve_tasks.json 등 : 받은 데이터 그대로
- cache/_meta.json         : 파일별 ETag(버전 표시)와 마지막 확인 시각
"""

import json
import re
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import quote

from app import i18n, paths

import requests

# SPT 공개 데이터: tarkov.dev 에 없는 퀘스트의 이름·맵·이벤트 목록만 보완한다.
# 로그에 나온 퀘스트에만 쓰고, 퀘스트를 새로 추가하지는 않는다.
# 받기·실패는 update_all() 과 따로 (SPT 가 안 돼도 tarkov.dev 데이터는 그대로).
SPT_BASE_URL = (
    "https://github.com/SP-Tushonka/server-csharp/raw/refs/heads/5.0x-dev/"
    "Libraries/SPTushonka.Server.Assets/SPT_Data/database/locales/global/"
)
SPT_QUESTS_URL = (
    "https://github.com/SP-Tushonka/server-csharp/raw/refs/heads/5.0x-dev/"
    "Libraries/SPTushonka.Server.Assets/SPT_Data/database/templates/quests.json"
)
SPT_QUEST_CONFIG_URL = (
    "https://github.com/SP-Tushonka/server-csharp/raw/refs/heads/5.0x-dev/"
    "Libraries/SPTushonka.Server.Assets/SPT_Data/configs/quest.json"
)
SPT_CHECK_SECONDS = 24 * 60 * 60
SPT_RETRY_SECONDS = 60 * 60
SPT_NAME_KEY = re.compile(r"([0-9a-fA-F]{24}) name")
_spt_update_lock = threading.Lock()
_spt_last_attempt = 0.0
_spt_loaded: tuple | None = None

KEY_NAME = re.compile(r"\bkey\b|keycard|key card", re.I)  # 영어 이름으로 열쇠·키카드 구분 (map_views 와 같은 규칙)

BASE_URL = "https://json.tarkov.dev"
GAME_MODE = "pve"  # PvE 모드

CACHE_DIR = paths.CACHE_DIR
META_FILE = CACHE_DIR / "_meta.json"
CHECK_HOURS = 1  # 이 시간마다 바뀐 게 있는지 확인

# 한 번에 받아 둘 파일들. "_ko"는 한국어 번역, "_en"은 번역이 없을 때 쓸 영어.
DATA_FILES = [
    "tasks", "tasks_ko", "tasks_en",
    "maps", "maps_ko", "maps_en",
    "traders", "traders_ko", "traders_en",
    "items_ko", "items_en",  # 아이템 이름 번역만 (아이템 전체 정보는 너무 커서 안 받음)
    "hideout", "hideout_ko", "hideout_en",  # 은신처 시설·레벨별 필요 재료 (약 100KB)
]

# 실패하면 기다리는 시간: 1분 → 2분 → 4분 ... 최대 60분
RETRY_START_SECONDS = 60
RETRY_MAX_SECONDS = 60 * 60


class DataError(Exception):
    """새로 받지도 못했고 저장본도 없을 때 쓰는 오류."""


# ---------- 저장 파일 다루기 ----------

def _cache_path(name: str) -> Path:
    return CACHE_DIR / f"{GAME_MODE}_{name}.json"


def _write_json(path: Path, value) -> None:
    CACHE_DIR.mkdir(exist_ok=True)
    # 임시 파일에 먼저 쓰고 바꿔치기 → 쓰는 도중 꺼져도 기존 파일이 안 깨짐
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    temp.replace(path)


def _read_spt_names() -> dict:
    """저장해 둔 작은 파일만 읽는다 (받기를 기다리지 않음)."""
    global _spt_loaded
    path = CACHE_DIR / "spt_quest_names.json"
    try:
        mtime = path.stat().st_mtime_ns
        hit = _spt_loaded
        if hit and hit[0] == mtime:
            return hit[1]
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        if not all(isinstance(data.get(lang), dict) for lang in ("ko", "en")):
            return {}
        _spt_loaded = (mtime, data)
        return data
    except (OSError, ValueError):
        return {}


def update_spt_names() -> None:
    """이름·위치·이벤트를 각각 하루마다 갱신하고, 실패하면 한 시간 뒤 다시 시도한다."""
    global _spt_last_attempt, _spt_loaded
    if not _spt_update_lock.acquire(blocking=False):
        return
    try:
        saved = _read_spt_names()
        now = time.time()

        def due(key: str) -> bool:
            checked_at = saved.get(key, 0)
            if not isinstance(checked_at, (int, float)):
                return True
            return now - checked_at >= SPT_CHECK_SECONDS

        names_due = due("checked_at")
        locations_due = due("location_checked_at")
        events_due = due("event_checked_at")
        if not names_due and not locations_due and not events_due:
            return
        if now - _spt_last_attempt < SPT_RETRY_SECONDS:
            return
        _spt_last_attempt = now
        names = {"ko": {}, "en": {}, **saved}
        changed = False
        if names_due:
            try:
                downloaded = {}
                blank = []
                for lang, filename in (("ko", "kr.json"), ("en", "en.json")):
                    with requests.get(SPT_BASE_URL + filename, timeout=60) as response:
                        response.raise_for_status()
                        locale = response.json()
                    if not isinstance(locale, dict):
                        raise ValueError("Invalid SPT locale")
                    downloaded[lang] = {
                        match[1]: value
                        for key, value in locale.items()
                        if (match := SPT_NAME_KEY.fullmatch(key))
                        and isinstance(value, str) and value.strip()
                    }
                    if not downloaded[lang]:
                        raise ValueError("Empty SPT names")
                    if lang == "en":
                        blank = [
                            match[1] for key, value in locale.items()
                            if (match := SPT_NAME_KEY.fullmatch(key))
                            and isinstance(value, str) and not value.strip()
                        ]
                # 두 언어가 모두 성공해야 이름과 그 확인 시각을 갱신한다.
                names.update(downloaded, blank=blank, checked_at=now)
                changed = True
            except Exception:
                pass  # 이름 실패와 관계없이 위치·이벤트는 별도로 시도한다.
        if locations_due:
            try:
                # 큰 원본은 스트리밍으로 임시 파일에 받고, 성공/실패 모두 닫으며 삭제한다.
                with tempfile.TemporaryFile() as source:
                    with requests.get(SPT_QUESTS_URL, timeout=60, stream=True) as response:
                        response.raise_for_status()
                        for chunk in response.iter_content(chunk_size=64 * 1024):
                            source.write(chunk)
                    source.seek(0)
                    quests = json.load(source)
                if not isinstance(quests, dict):
                    raise ValueError("Invalid SPT quests")
                locations = {
                    quest_id: quest["location"]
                    for quest_id, quest in quests.items()
                    if isinstance(quest, dict)
                    and isinstance(quest.get("location"), str) and quest["location"].strip()
                }
                if not locations:
                    raise ValueError("Empty SPT locations")
                names.update(location=locations, location_checked_at=now)
                changed = True
            except Exception:
                pass  # 이전 위치를 유지하고, 성공한 이름은 저장한다.
        if events_due:
            try:
                with requests.get(SPT_QUEST_CONFIG_URL, timeout=60) as response:
                    response.raise_for_status()
                    config = response.json()
                if not isinstance(config, dict) or not isinstance(config.get("eventQuests"), dict):
                    raise ValueError("Invalid SPT event quests")
                names.update(event=list(config["eventQuests"]), event_checked_at=now)
                changed = True
            except Exception:
                pass  # 이전 이벤트 목록을 유지하고, 성공한 이름·위치는 저장한다.
        if not changed:
            return
        CACHE_DIR.mkdir(exist_ok=True)
        path = CACHE_DIR / "spt_quest_names.json"
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(names, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(path)
        _spt_loaded = None
    except Exception:
        pass  # 이름 보완 실패가 실행이나 퀘스트 목록을 막지 않게 한다.
    finally:
        _spt_update_lock.release()


def _english_item_name(item_id: str) -> str:
    try:
        name = load("items_en").get(f"{item_id} Name", "")
        return name.strip() if isinstance(name, str) else ""
    except DataError:
        return ""


def key_wiki(item_id: str) -> str | None:
    """열쇠의 공식 위키 주소. 위키 페이지 이름 = 영어 아이템 이름 (공백 → _)
    '#' 은 위키 페이지 이름에 못 써서 뺌 (예: Object #11SR keycard → Object_11SR_keycard)
    2026-11-20 퀘스트에 나오는 열쇠 69개 모두 실제 페이지 확인"""
    name = _english_item_name(item_id)
    if not name:
        return None
    title = " ".join(name.replace("#", "").split()).replace(" ", "_")
    return "https://escapefromtarkov.fandom.com/wiki/" + quote(title, safe="()!'")


def is_key(item_id: str) -> bool:
    return bool(KEY_NAME.search(_english_item_name(item_id)))


def spt_blank_name(quest_id: str) -> bool:
    """SPT 영어 이름이 빈 퀘스트인지 확인한다. 이전 캐시는 빈 목록으로 본다."""
    return quest_id in _read_spt_names().get("blank", [])


def spt_event_quest(quest_id: str) -> bool:
    """SPT 이벤트 퀘스트인지 확인한다. 이전 캐시는 빈 목록으로 본다."""
    events = _read_spt_names().get("event", [])
    return isinstance(events, list) and quest_id in events


def spt_quest_location(quest_id: str) -> str | None:
    """저장된 퀘스트 맵 id. 옛 저장본에는 맵이 없을 수 있다."""
    locations = _read_spt_names().get("location", {})
    if isinstance(locations, dict):
        location = locations.get(quest_id)
        if isinstance(location, str) and location.strip():
            return location
    return None


def spt_quest_name(quest_id: str, lang: str) -> str | None:
    """저장된 이름 그대로. 한국어가 없으면 영어."""
    names = _read_spt_names()
    for language in (("ko", "en") if lang == "ko" else ("en",)):
        name = names.get(language, {}).get(quest_id)
        if isinstance(name, str) and name.strip():
            return name
    return None


def _read_meta() -> dict:
    """{"files": {이름: {"etag": ...}}, "checked_at": 시각, "retry_wait": 초, "next_try": 시각}"""
    try:
        meta = json.loads(META_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        meta = {}
    meta.setdefault("files", {})
    meta.setdefault("checked_at", 0)
    meta.setdefault("retry_wait", 0)
    meta.setdefault("next_try", 0)
    return meta


def _all_saved() -> bool:
    return all(_cache_path(name).exists() for name in DATA_FILES)


def _time_text(when: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(when))


# ---------- 확인하고 받아오기 ----------

def _fetch_if_changed(name: str, etag: str | None):
    """바뀌었으면 (새 데이터, 새 ETag), 안 바뀌었으면 (None, 기존 ETag)."""
    headers = {"If-None-Match": etag} if etag else {}
    url = f"{BASE_URL}/{GAME_MODE}/{name}"
    response = requests.get(url, headers=headers, timeout=60)
    if response.status_code == 304:  # "그대로예요"
        return None, etag
    response.raise_for_status()  # 404, 500 같은 오류면 여기서 멈춤
    return response.json()["data"], response.headers.get("ETag")


def update_all(force: bool = False) -> str:
    """필요하면 바뀐 파일만 새로 받는다. 안내 메시지를 돌려준다.

    force=True면 확인 시각과 상관없이 지금 바로 확인한다.
    """
    meta = _read_meta()
    saved = _all_saved()
    now = time.time()

    if saved and not force and now - meta["checked_at"] < CHECK_HOURS * 3600:
        return i18n.t("data.usingSaved", time=_time_text(meta["checked_at"]))

    if saved and not force and now < meta["next_try"]:
        minutes = int((meta["next_try"] - now) // 60) + 1
        return i18n.t("data.retryLater", minutes=minutes, time=_time_text(meta["checked_at"]))

    try:
        results = {}
        for name in DATA_FILES:
            # 저장 파일이 없으면 ETag를 보내지 않음 → 무조건 새로 받음
            etag = meta["files"].get(name, {}).get("etag") if _cache_path(name).exists() else None
            results[name] = _fetch_if_changed(name, etag)
    except Exception as error:  # 인터넷 끊김, 서버 장애 등
        meta["retry_wait"] = min(max(meta["retry_wait"] * 2, RETRY_START_SECONDS), RETRY_MAX_SECONDS)
        meta["next_try"] = now + meta["retry_wait"]
        _write_json(META_FILE, meta)
        if saved:
            return i18n.t("data.checkFailed", error=error, time=_time_text(meta["checked_at"]))
        raise DataError(i18n.t("data.noData", error=error)) from error

    # 전부 확인에 성공했을 때만 저장 → 일부만 새것이 되는 일이 없음
    changed = []
    for name, (data, etag) in results.items():
        if data is not None:
            _write_json(_cache_path(name), data)
            changed.append(name)
        meta["files"][name] = {"etag": etag}
    meta.update(checked_at=now, retry_wait=0, next_try=0)
    _write_json(META_FILE, meta)

    if changed:
        return i18n.t("data.updated", names=", ".join(changed))
    return i18n.t("data.upToDate")


_loaded: dict = {}  # 파일 이름 → (수정 시각, 내용). 같은 파일을 여러 번 읽지 않게


def load(name: str):
    """저장된 파일 하나의 내용을 돌려준다. (먼저 update_all을 불러야 함)"""
    path = _cache_path(name)
    try:
        mtime = path.stat().st_mtime
        hit = _loaded.get(name)
        if hit and hit[0] == mtime:
            return hit[1]
        data = json.loads(path.read_text(encoding="utf-8"))
        _loaded[name] = (mtime, data)
        return data
    except (FileNotFoundError, json.JSONDecodeError, OSError) as error:
        raise DataError(i18n.t("data.notSaved", name=name)) from error


# ---------- 번역 ----------

class Translator:
    """'5ae448bf... name' 같은 번역 키를 지금 고른 언어(한국어·영어)로 바꿔준다.

    그 언어 번역이 없으면 영어, 영어도 없으면 키를 그대로 돌려준다.
    """

    def __init__(self, kind: str, lang: str | None = None):
        self.lang = lang or i18n.current_lang()
        self.main = load(f"{kind}_{self.lang}")
        self.en = load(f"{kind}_en")

    def __call__(self, key: str) -> str:
        return self.main.get(key) or self.en.get(key) or key


def get_tasks() -> tuple[dict, Translator]:
    """퀘스트 사전(id → 정보)과 번역기를 돌려준다."""
    return load("tasks")["tasks"], Translator("tasks")


# 이 파일을 직접 실행하면 간단한 테스트를 한다.
if __name__ == "__main__":
    import sys

    try:
        print(update_all(force="--force" in sys.argv))
        tasks, tr = get_tasks()
    except DataError as error:
        print(f"❌ {error}")
        raise SystemExit(1)

    print(f"퀘스트 개수: {len(tasks)}개\n")
    for task in list(tasks.values())[:3]:
        print(f"■ {tr(task['name'])}  (레벨 {task['minPlayerLevel']}+)")
        for obj in task["objectives"]:
            print(f"   - {tr(obj['description'])}")
        print()

"""tarkov.dev의 전체 데이터 파일(json.tarkov.dev)을 받아서 저장해 두는 파일.

동작 방식
1. 저장해 둔 파일(cache/)이 있으면 그걸 바로 쓴다 → 프로그램이 빨리 켜짐
2. 한 시간(CHECK_HOURS)마다 "바뀐 게 있나요?"만 물어본다 (ETag 확인)
   - 안 바뀌었으면 서버가 "그대로예요(304)"라고만 답함 → 거의 0바이트
   - 바뀐 파일만 새로 받는다
3. 확인에 실패하면 저장본을 계속 쓰고, 언제 정보인지 안내한다
4. 실패한 뒤에는 바로 또 시도하지 않고 점점 오래 기다린다 (서버 배려)

저장 파일
- cache/pve_tasks.json 등 : 받은 데이터 (맵·아이템 번역은 사용하는 필드만)
- cache/_meta.json         : 파일별 ETag(버전 표시)와 마지막 확인 시각
"""

import json
import math
import os
import queue
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
_data_update_lock = threading.Lock()
_data_update_count = 0
# 갱신 작업자만 직렬화한다. 정상 저장본을 읽는 요청은 이 잠금을 기다리지 않는다.
_update_all_lock = threading.Lock()

# 계측은 켰을 때만 별도 일꾼이 쓴다. 요청 스레드는 디스크 쓰기를 기다리지 않는다.
PROFILE_STARTUP = os.environ.get("RAIDBRIEFER_PROFILE_STARTUP") == "1"
_profile_origin = time.perf_counter()
_profile_queue = queue.Queue(maxsize=4096)
_profile_local = threading.local()


def profile_event(event: str, **fields) -> None:
    if not PROFILE_STARTUP:
        return
    try:
        _profile_queue.put_nowait({"ms": round((time.perf_counter() - _profile_origin) * 1000, 3),
                                  "event": event, **fields})
    except Exception:
        pass


def profile_start(origin: float) -> None:
    global _profile_origin
    if not PROFILE_STARTUP:
        return
    _profile_origin = origin
    origin_name = "server_import"
    started_at = time.time() - (time.perf_counter() - origin)
    # 위험 파일의 진입점을 바꾸지 않고 현재 서버 프로세스의 생성 시각만 읽는다.
    # 이후 모든 간격은 perf_counter를 사용한다. Windows 외에는 import 시점이 기준이다.
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.GetCurrentProcess.restype = wintypes.HANDLE
            kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
            created, exited, kernel_time, user_time = (wintypes.FILETIME() for _ in range(4))
            if kernel.GetProcessTimes(kernel.GetCurrentProcess(), ctypes.byref(created), ctypes.byref(exited),
                                      ctypes.byref(kernel_time), ctypes.byref(user_time)):
                started_at = ((created.dwHighDateTime << 32) | created.dwLowDateTime) / 10_000_000 - 11644473600
                _profile_origin = time.perf_counter() - max(0, time.time() - started_at)
                origin_name = "windows_process_creation"
        except Exception:
            pass

    def write():
        try:
            paths.USER_DIR.mkdir(parents=True, exist_ok=True)
            with (paths.USER_DIR / "startup_profile.log").open("a", encoding="utf-8") as stream:
                stream.write("\n" + json.dumps({"start": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(started_at)),
                                               "pid": os.getpid(), "origin": origin_name}) + "\n")
                stream.flush()
                while True:
                    stream.write(json.dumps(_profile_queue.get(), ensure_ascii=False) + "\n")
                    stream.flush()
        except Exception:
            pass

    try:
        threading.Thread(target=write, name="startup-profile", daemon=True).start()
        profile_event("process_start", started_ms=0)
        profile_event("server_import", started_ms=(origin - _profile_origin) * 1000)
    except Exception:
        pass


def profile_wait(name: str, started: float) -> None:
    if PROFILE_STARTUP:
        try:
            elapsed = (time.perf_counter() - started) * 1000
            waits = getattr(_profile_local, "waits", None)
            if waits is not None:
                waits[name] = waits.get(name, 0) + elapsed
        except Exception:
            pass


class ProfileLock:
    """계측을 켠 실행에서만 기존 잠금을 감싼다 (잠금 동작은 그대로)."""
    def __init__(self, lock, name):
        self.lock, self.name = lock, name

    def acquire(self, *args, **kwargs):
        started = time.perf_counter()
        acquired = self.lock.acquire(*args, **kwargs)
        if acquired:
            profile_wait(self.name, started)
        return acquired

    def release(self):
        return self.lock.release()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *args):
        self.release()


def _data_activity(delta: int) -> None:
    global _data_update_count
    with _data_update_lock:
        _data_update_count += delta


def is_updating() -> bool:
    """Concurrent tarkov.dev / SPT downloads share a nonblocking status flag."""
    with _data_update_lock:
        return _data_update_count > 0

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

MAP_CACHE_FIELDS = frozenset({
    "id", "name", "normalizedName", "scenePath", "wiki", "raidDuration", "accessKeys",
    "extracts", "transits", "locks", "lootLoose", "bosses", "btrStops",
})
COMPACT_CACHE_FILES = {"maps", "maps_ko", "maps_en", "items_ko", "items_en"}

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
    temp = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=path.name + ".", suffix=".tmp", delete=False) as stream:
            temp = Path(stream.name)
            json.dump(value, stream, ensure_ascii=False)
        for attempt in range(5):
            try:
                temp.replace(path)
                return
            except PermissionError:
                # Windows: 읽기/백신의 일시 잠금. 원본을 지우지 않고 교체만 재시도한다.
                if attempt == 4:
                    raise
                time.sleep(0.2)
    finally:
        if temp is not None:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass


def _compact_cache(name: str, data):
    """필드 이름·순서를 유지하며 사용하지 않는 큰 데이터만 제외한다."""
    if name in {"maps", "maps_ko", "maps_en"}:
        # maps_ko/en은 현재 번역 사전이므로 그대로 둔다. 구조가 같을 때만 맵처럼 줄인다.
        if isinstance(data, dict) and isinstance(data.get("maps"), dict):
            return {"maps": {
                map_id: {key: value for key, value in info.items() if key in MAP_CACHE_FIELDS}
                for map_id, info in data["maps"].items()
            }}
    elif name in {"items_ko", "items_en"}:
        # Translator("items")와 영어 이름 조회는 Name만 사용한다 (ShortName은 사용 안 함).
        return {key: value for key, value in data.items() if key.endswith(" Name")}
    return data


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
    downloading = False
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
        _data_activity(1)
        downloading = True
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
        if downloading:
            _data_activity(-1)
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
    except (ValueError, OSError):
        meta = {}
    if not isinstance(meta, dict):
        meta = {}
    files = meta.get("files")
    meta["files"] = {
        name: {"etag": entry.get("etag") if isinstance(entry.get("etag"), str) else None}
        for name, entry in (files.items() if isinstance(files, dict) else [])
        if isinstance(entry, dict)
    }
    for key in ("checked_at", "retry_wait", "next_try"):
        value = meta.get(key)
        if type(value) not in (int, float) or not 0 <= value <= 253402300799 or not math.isfinite(value):
            value = 0
        meta[key] = value
    # 시계가 뒤로 바뀐 경우 미래 시각 때문에 영구히 확인을 건너뛰지 않는다.
    now = time.time()
    if meta["checked_at"] > now:
        meta["checked_at"] = 0
    if meta["next_try"] > now + RETRY_MAX_SECONDS:
        meta["next_try"] = 0
    return meta


def _all_saved() -> bool:
    return all(_usable(name) for name in DATA_FILES)


def _usable(name: str) -> bool:
    try:
        _read_saved(name)
        return True
    except DataError:
        return False


def _validate_data(name: str, data) -> None:
    if not isinstance(data, dict):
        raise ValueError(i18n.t("data.notSaved", name=name))
    if name in ("tasks", "maps"):
        entries = data.get(name)
    elif name in ("traders", "hideout"):
        entries = data
    else:
        if not all(value is None or isinstance(value, str) for value in data.values()):
            raise ValueError(i18n.t("data.notSaved", name=name))
        return
    if not isinstance(entries, dict) or not all(isinstance(value, dict) for value in entries.values()):
        raise ValueError(i18n.t("data.notSaved", name=name))


def _time_text(when: float) -> str:
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(when))
    except (OSError, OverflowError, ValueError):
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(0))


# ---------- 확인하고 받아오기 ----------

def _fetch_if_changed(name: str, etag: str | None):
    """바뀌었으면 (새 데이터, 새 ETag), 안 바뀌었으면 (None, 기존 ETag)."""
    headers = {"If-None-Match": etag} if etag else {}
    url = f"{BASE_URL}/{GAME_MODE}/{name}"
    started = time.perf_counter()
    try:
        response = requests.get(url, headers=headers, timeout=60)
    finally:
        profile_event("data_network", file=name, duration_ms=(time.perf_counter() - started) * 1000)
    with response:
        if response.status_code == 304:
            if not etag:
                raise ValueError(i18n.t("data.notSaved", name=name))
            return None, etag
        response.raise_for_status()
        started = time.perf_counter()
        try:
            data = response.json()["data"]
            _validate_data(name, data)
            return data, response.headers.get("ETag")
        finally:
            profile_event("data_parse", file=name, duration_ms=(time.perf_counter() - started) * 1000)


def _error_summary(error: Exception) -> str:
    """화면·신고에 보일 실패 이유: 오류 종류 + HTTP 번호만 (원문에는 경로·프록시 주소가 섞일 수 있음)"""
    status = getattr(getattr(error, "response", None), "status_code", None)
    name = type(error).__name__
    return f"{name} {status}" if isinstance(status, int) else name


_last_error = None
_retry_after = 0.0  # 실행 중 재시도 간격은 PC 시계 변경의 영향을 받지 않는다.
_data_revision = 0


def data_revision() -> int:
    return _data_revision


def update_failed() -> bool:
    """마지막 데이터 받기가 실패했는지 (알림 DATA-01). 성공하면 다시 False"""
    return _last_error is not None


def data_message() -> str:
    meta = _read_meta()
    if _last_error:
        return i18n.t("data.checkFailed", error=_last_error, time=_time_text(meta["checked_at"]))
    if time.time() < meta["next_try"]:
        minutes = int((meta["next_try"] - time.time()) // 60) + 1
        return i18n.t("data.retryLater", minutes=minutes, time=_time_text(meta["checked_at"]))
    return i18n.t("data.usingSaved", time=_time_text(meta["checked_at"]))


def update_all(force: bool = False) -> str:
    """저장본을 먼저 사용하고 갱신은 하나의 작업자에게 맡긴다.

    없는 파일의 대기는 load()에서만 한다. force=True는 명시적 동기 갱신이다.
    """
    global _last_error, _retry_after
    if not _update_all_lock.acquire(blocking=False):
        if force:
            started = time.perf_counter()
            with _update_all_lock:
                profile_wait("data_refresh", started)
            if not _all_saved():
                raise DataError(i18n.t("data.noData", error=_last_error or i18n.t("common.unknownError")))
        return data_message()
    handed_off = False
    try:
        meta = _read_meta()
        if not force:
            retry_pending = (time.monotonic() < _retry_after if _retry_after
                             else time.time() < meta["next_try"])
            if retry_pending:
                return data_message()
            if 0 <= time.time() - meta["checked_at"] < CHECK_HOURS * 3600 and _all_saved():
                profile_event("update_all_start", force=False)
                for name in DATA_FILES:
                    profile_event("data_skipped_fresh", file=name)
                profile_event("update_all_end", skipped="fresh", failed=False)
                return data_message()
        if force:
            message = _update_all(force)
            if not _all_saved():
                raise DataError(i18n.t("data.noData", error=_last_error or i18n.t("common.unknownError")))
            return message
        # 상태 플래그는 thread 시작 전에 올려 매우 빠른 첫 poll도 놓치지 않게 한다.
        _data_activity(1)
        try:
            threading.Thread(target=_refresh_worker, name="tarkov-data", daemon=True).start()
        except Exception as error:
            _data_activity(-1)
            _last_error = _error_summary(error)
            _retry_after = time.monotonic() + RETRY_START_SECONDS
            return data_message()
        handed_off = True
        return data_message()
    finally:
        if not handed_off:
            _update_all_lock.release()


def _refresh_worker() -> None:
    try:
        _update_all(False)
    except Exception:
        pass  # 오류 내용과 재시도 시각은 _update_all에서 보존한다.
    finally:
        _data_activity(-1)
        _update_all_lock.release()


def _update_all(force: bool) -> str:
    global _last_error, _retry_after, _data_revision
    meta = _read_meta()
    started = time.perf_counter()
    profile_event("update_all_start", force=force)
    _data_activity(1)
    try:
        results = {}
        for name in DATA_FILES:
            # 존재 여부만으로 판단하지 않는다. 손상 본문에는 옛 ETag를 보내지 않는다.
            etag = meta["files"].get(name, {}).get("etag") if _usable(name) else None
            results[name] = _fetch_if_changed(name, etag)
        changed = []
        for name, (data, etag) in results.items():
            write_started = time.perf_counter()
            try:
                if data is not None:
                    _write_json(_cache_path(name), _compact_cache(name, data))
                    _loaded.pop(name, None)
                    _data_revision += 1
                    changed.append(name)
                elif name in COMPACT_CACHE_FILES:
                    cached = _read_saved(name)
                    compact = _compact_cache(name, cached)
                    if compact != cached:
                        _write_json(_cache_path(name), compact)
                        _loaded.pop(name, None)
                        _data_revision += 1
                meta["files"][name] = {"etag": etag}
            finally:
                profile_event("data_write", file=name, duration_ms=(time.perf_counter() - write_started) * 1000)
        # 마지막 성공 시각은 본문과 메타데이터를 모두 저장했을 때만 갱신한다.
        updated_meta = {**meta, "checked_at": time.time(), "retry_wait": 0, "next_try": 0}
        _write_json(META_FILE, updated_meta)
        _last_error = None
        _retry_after = 0.0
        return i18n.t("data.updated", names=", ".join(changed)) if changed else i18n.t("data.upToDate")
    except Exception as error:  # 인터넷 끊김, 서버 장애 등
        _last_error = _error_summary(error)
        meta["retry_wait"] = min(max(meta["retry_wait"] * 2, RETRY_START_SECONDS), RETRY_MAX_SECONDS)
        meta["next_try"] = time.time() + meta["retry_wait"]
        _retry_after = time.monotonic() + meta["retry_wait"]
        try:
            _write_json(META_FILE, meta)
        except Exception:
            pass  # 쓰기 금지/잠금이어도 기존 저장본은 계속 제공한다.
        if _all_saved():
            return data_message()
        raise DataError(i18n.t("data.noData", error=_last_error)) from error
    finally:
        _data_activity(-1)
        _data_revision += 1  # 304와 실패 안내도 퀘스트 응답 cache에 반영한다.
        profile_event("update_all_end", duration_ms=(time.perf_counter() - started) * 1000,
                      failed=bool(_last_error))


_loaded: dict = {}  # 파일 이름 → (수정 시각, 내용). 같은 파일을 여러 번 읽지 않게


def load(name: str):
    """유효한 파일은 갱신 잠금을 잡지 않고 읽는다. 없는 파일만 기다린다."""
    try:
        return _read_saved(name)
    except DataError:
        update_all()
        started = time.perf_counter()
        with _update_all_lock:
            profile_wait("data_refresh", started)
        try:
            return _read_saved(name)
        except DataError as error:
            if _last_error:
                raise DataError(i18n.t("data.noData", error=_last_error)) from error
            raise


def _read_saved(name: str):
    path = _cache_path(name)
    for attempt in range(3):
        try:
            stat = path.stat()
            mtime = (stat.st_mtime_ns, stat.st_size)
            hit = _loaded.get(name)
            if hit and hit[0] == mtime:
                return hit[1]
            data = json.loads(path.read_text(encoding="utf-8"))
            _validate_data(name, data)
            _loaded[name] = (mtime, data)
            return data
        except PermissionError as error:
            hit = _loaded.get(name)
            if hit:
                return hit[1]  # 백신의 일시 잠금 동안 이미 확인한 메모리 저장본 사용
            # Windows: 다른 쪽이 막 새 파일로 바꾸는 중이면 잠깐 못 읽음 → 조금 뒤 다시
            if attempt == 2:
                raise DataError(i18n.t("data.notSaved", name=name)) from error
            time.sleep(0.2)
        except (ValueError, OSError) as error:
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

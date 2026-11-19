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
import time
from pathlib import Path

from app import i18n, paths

import requests

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

"""알림함: 사용자가 알아야 할 문제를 고정 코드로 모은다 (2026-11-23 사용자, docs/notes/discord.md).

화면에는 코드만 보낸다 (뜻·문장은 화면 사전). 오류 원문·경로·로그 내용은 보내지 않는다.
- LOG-01 게임 로그 형식을 못 읽음        (map_watch.log_problem "unreadable", 게임 켜져 있을 때)
- LOG-02 레이드 시작은 보이는데 맵 모름   (map_watch.log_problem "no_map", 게임 켜져 있을 때)
- LOG-03 퀘스트 기록을 못 읽음           (map_watch.notification_problem, 게임 켜져 있을 때)
- DATA-01 tarkov.dev 새로 받기 실패      (저장본으로 동작 중)
- SAVE-01 내 기록·설정 파일 손상         (settings.read_saved)
- UPD-01 업데이트 확인 실패              (다시 시도까지 모두 실패했을 때만)
개발용(소스 실행일 때만): 번역 필요 개수 (tools/ko_missing.py --all 과 같은 기준)
"""

import platform
import re
import sys
import threading
import time

from app import map_watch, settings, tarkov_api, updater
from app.version import VERSION

HANGUL = re.compile("[가-힣]")
DEV = not getattr(sys, "frozen", False)  # exe 가 아니면 개발용 (editor.py 와 같은 규칙)
TRANSLATION_RECHECK_SECONDS = 30  # 개발 중 ko_overrides.json 을 고치면 30초 안에 반영


def collect(log_problem: str | None, game_running: bool) -> list[str]:
    """지금 켜진 알림 코드 목록 (순서 = 화면 순서). 디스크를 읽지 않는다 (2초마다 불림)"""
    codes = []
    if log_problem == "unreadable":
        codes.append("LOG-01")
    elif log_problem == "no_map":
        codes.append("LOG-02")
    if game_running and map_watch.notification_problem():
        codes.append("LOG-03")
    if tarkov_api.update_failed():
        codes.append("DATA-01")
    if settings.saved_read_failed():
        codes.append("SAVE-01")
    update = updater.state()
    if update.get("check_finished") and update.get("check_error") and not update.get("latest"):
        codes.append("UPD-01")
    return codes


# ---------- 개발용: 번역 필요 개수 ----------
_dev_lock = threading.Lock()
_dev = {"count": None, "key": None, "checked": 0.0, "busy": False}


def _count_translation_needed() -> int:
    from app import quests  # 서버 시작을 느리게 하지 않게 필요할 때만
    tasks = tarkov_api.load("tasks")["tasks"]
    tr = tarkov_api.Translator("tasks", lang="ko")
    overrides = quests._load_overrides()
    count = 0
    for task in tasks.values():
        for objective in task.get("objectives", []):
            key = objective["description"]
            if HANGUL.search(tr.main.get(key) or ""):
                continue  # 게임 공식 한국어 있음
            ours = overrides.get(key)
            if not (ours and ours.get("en") == (tr.en.get(key) or key)):
                count += 1  # 우리 번역 없음 또는 원문이 바뀜
    return count


def _refresh_translation(key) -> None:
    try:
        count = _count_translation_needed()
    except Exception:
        count = None  # 데이터가 아직 없으면 다음에 다시
    with _dev_lock:
        _dev.update(count=count, key=key if count is not None else None, busy=False)


def dev_info() -> dict | None:
    """개발용 정보. exe 에서는 None (계산도 안 함)"""
    if not DEV:
        return None
    now = time.monotonic()
    with _dev_lock:
        start = not _dev["busy"] and now - _dev["checked"] >= TRANSLATION_RECHECK_SECONDS
        if start:
            _dev.update(busy=True, checked=now)
        count = _dev["count"]
    if start:
        threading.Thread(target=_check_translation_key, name="dev-translation", daemon=True).start()
    return {"translation_needed": count}


def _check_translation_key() -> None:
    """데이터·번역 파일이 그대로면 다시 세지 않는다"""
    from app import quests
    try:
        key = (tarkov_api.data_revision(), quests.OVERRIDES_FILE.stat().st_mtime_ns)
    except (AttributeError, OSError):
        key = (tarkov_api.data_revision(), None)
    with _dev_lock:
        same = _dev["key"] == key and _dev["count"] is not None
        if same:
            _dev["busy"] = False
    if not same:
        _refresh_translation(key)


def game_version() -> str | None:
    return map_watch.game_version()


def report_info() -> dict:
    """신고 버튼용 정보 (2026-11-23 사용자). 버전 숫자만: 사용자 이름·폴더 경로·로그 내용은 넣지 않는다"""
    try:
        windows = f"Windows {platform.release()} ({platform.version()})"
    except Exception:
        windows = ""
    return {"version": VERSION, "windows": windows, "game_version": game_version() or ""}

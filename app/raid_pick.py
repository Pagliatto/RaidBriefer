"""'이번 판' 퀘스트 고르기 (2026-11-28 사용자).

홈 '할 일'·지도 서랍에서 이번 레이드에 할 퀘스트를 고르면 지도 핀·준비물이 고른 것만 보인다.
- 아무것도 안 고르면 지금과 똑같음
- 고른 순서가 지도 번호(1, 2, 3…)가 되므로 목록 순서를 그대로 저장한다
- 레이드 한 번 하고 나오면 무조건 풀림 (매 판 준비물 체크하듯). 트랜짓으로 맵만 바뀌는 동안은 유지
  → 로그의 '레이드 끝'(map_watch.last_raid_end)이 고른 때보다 뒤면 **바로** 비우고 지난 고르기를 backup 에 둔다.
    트랜짓도 끝 줄이 먼저 찍히므로, 나중에 그 끝이 트랜짓으로 밝혀지면(map_watch.was_transit) backup 을 되살린다.
    (로딩 시간으로 기다리지 않음: 서버가 느려 트랜짓 줄이 늦게 와도 결국 되살아남)
    프로그램이 꺼진 사이 끝난 레이드도, 다시 켤 때 그 폴더 로그를 처음부터 읽으므로 같은 규칙으로 풀린다.
- '전체 보기'(show_all)도 레이드가 끝나면 함께 풀림
- 저장 위치: user_data/raid_pick.json (한 판짜리 기록. 손상되면 빈 상태로 시작해도 잃을 게 적음)
"""

import threading
from datetime import datetime

from app import map_watch, paths, settings

PICK_FILE = paths.USER_DIR / "raid_pick.json"
MAX_PICKS = 200
_lock = threading.Lock()
_data = None  # 처음 쓸 때 파일에서 읽음


def _empty(cleared=False) -> dict:
    return {"quests": [], "show_all": False, "picked_at": None, "cleared": cleared, "backup": None}


def _valid_quests(quests) -> bool:
    return (isinstance(quests, list) and len(quests) <= MAX_PICKS
            and all(isinstance(q, str) and 0 < len(q) <= 100 for q in quests)
            and len(set(quests)) == len(quests))


def _valid_time(value) -> bool:
    if value is None:
        return True
    try:
        datetime.fromisoformat(value)
        return isinstance(value, str)
    except (TypeError, ValueError):
        return False


def _valid(data: dict) -> bool:
    backup = data.get("backup")
    if backup is not None and not (isinstance(backup, dict) and _valid_quests(backup.get("quests"))
                                   and isinstance(backup.get("show_all"), bool)
                                   and isinstance(backup.get("end"), str) and _valid_time(backup["end"])):
        return False
    return (_valid_quests(data.get("quests")) and _valid_time(data.get("picked_at"))
            and isinstance(data.get("show_all", False), bool) and isinstance(data.get("cleared", False), bool))


def _load() -> dict:
    global _data
    if _data is None:
        try:
            saved = settings.read_saved(PICK_FILE, _valid)
        except settings.DataReadError:
            saved = {}  # 손상 파일은 settings 가 따로 백업해 둠. 한 판짜리 기록이라 빈 상태로 시작
        _data = {**_empty(), **saved}
    return _data


def _save() -> None:
    settings.write_saved(PICK_FILE, _data)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _follow_log() -> bool:
    """레이드 끝·트랜짓에 맞춰 비우거나 되살린다. 바뀌었으면 True"""
    data = _data
    backup = data["backup"]
    if backup and map_watch.was_transit(datetime.fromisoformat(backup["end"])):
        # 트랜짓이었음 → 지난 고르기 되살림. 그사이 새로 고른 것은 뒤에 붙임
        quests = backup["quests"] + [q for q in data["quests"] if q not in backup["quests"]]
        data.update(quests=quests[:MAX_PICKS], show_all=(backup["show_all"] or data["show_all"]) and bool(quests),
                    # 고른 때 = 그 트랜짓 끝. 그 뒤의 진짜 끝은 (로그를 한꺼번에 읽는 경우에도) 풀리게
                    picked_at=backup["end"] if quests else None, cleared=False, backup=None)
        return True
    if not (data["quests"] or data["show_all"]) or not data["picked_at"]:
        return False
    end = map_watch.last_raid_end()
    if end is None or end <= datetime.fromisoformat(data["picked_at"]):
        return False
    old = {"quests": list(data["quests"]), "show_all": data["show_all"], "end": end.isoformat()}
    data.update(_empty(cleared=True))
    data["backup"] = old  # 트랜짓으로 밝혀지면 되살림 (다음 진짜 끝에서 새 backup 으로 바뀜)
    return True


def _snapshot() -> dict:
    return {"quests": list(_data["quests"]), "show_all": _data["show_all"], "cleared": _data["cleared"]}


def current() -> dict:
    """화면에 줄 지금 상태: quests(고른 순서), show_all, cleared(레이드가 끝나서 방금 풀림 안내)"""
    with _lock:
        _load()
        if _follow_log():
            try:
                _save()
            except settings.SaveWriteError:
                pass  # 메모리에서는 이미 바뀜. 저장 실패는 알림함 SAVE-02 로 보임
        return _snapshot()


def change(*, quest=None, on=None, show_all=None, clear=False) -> dict:
    """quest+on: 고르기/풀기, show_all: 전체 보기 켬/끔, clear: 모두 풀기. 바뀐 상태를 돌려준다"""
    with _lock:
        _load()
        _follow_log()
        if clear:
            _data.update(_empty())  # 직접 푼 것은 트랜짓이어도 되살리지 않음 (backup 도 비움)
        if quest is not None:
            if on and quest not in _data["quests"]:
                if len(_data["quests"]) >= MAX_PICKS:
                    raise ValueError("too many picks")
                _data["quests"].append(quest)  # 레이드 중에 더 고르면 다음 번호
            elif not on and quest in _data["quests"]:
                _data["quests"].remove(quest)
            _data["cleared"] = False
        if show_all is not None:
            _data["show_all"] = bool(show_all)
        if not _data["quests"]:
            _data["show_all"] = False
        # 고른 때 = 마지막으로 바꾼 때. 이보다 뒤에 끝난 레이드가 있으면 풀림
        _data["picked_at"] = _now() if _data["quests"] else None
        _save()
        return _snapshot()

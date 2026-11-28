"""'이번 판' 퀘스트 고르기 (2026-11-28 사용자).

홈 '할 일'·지도 서랍에서 이번 레이드에 할 퀘스트를 고르면 지도 핀·준비물이 고른 것만 보인다.
- 아무것도 안 고르면 지금과 똑같음
- 고른 순서가 지도 번호(1, 2, 3…)가 되므로 목록 순서를 그대로 저장한다
- 레이드 한 번 하고 나오면 무조건 풀림 (매 판 준비물 체크하듯). 트랜짓으로 맵만 바뀌는 동안은 유지
  → 고를 때 알던 마지막 '레이드 끝'(end_seen, 로그 시각)과 지금의 마지막 끝(map_watch.last_raid_end)이 **다르면**
    새 끝이 생긴 것 → 바로 비우고 지난 고르기를 backup 에 둔다. 로그 시각끼리만 비교하므로 PC 시계를 바꿔도 맞음 (Codex 106 #3).
    트랜짓도 끝 줄이 먼저 찍히므로, 나중에 그 끝이 트랜짓으로 밝혀지면(map_watch.was_transit) backup 을 되살린다.
    (로딩 시간으로 기다리지 않음: 서버가 느려 트랜짓 줄이 늦게 와도 결국 되살아남)
- 프로그램이 꺼진 사이 끝난 레이드: 지금 로그 폴더는 감시가 처음부터 읽고, 그 전 폴더(게임을 다시 켠 경우)는
  처음 불러올 때 고른 때 뒤의 폴더를 한 번 훑는다 (map_watch.raid_ended_since, Codex 106 #4)
- '전체 보기'(show_all)도 레이드가 끝나면 함께 풀림
- 저장 위치: user_data/raid_pick.json. 잠겨서 못 읽으면 덮어쓰지 않고 다시 읽음, 백업된 손상 파일만 새로 씀 (Codex 106 #2)
"""

import threading
from datetime import datetime

from app import map_watch, paths, settings

PICK_FILE = paths.USER_DIR / "raid_pick.json"
MAX_PICKS = 200
_lock = threading.Lock()
_data = None  # 처음 쓸 때 파일에서 읽음 (못 읽으면 None 그대로 → 다음에 다시)


def _empty(cleared=False) -> dict:
    return {"quests": [], "show_all": False, "picked_at": None, "end_seen": None, "cleared": cleared, "backup": None}


def _valid_quests(quests) -> bool:
    return (isinstance(quests, list) and len(quests) <= MAX_PICKS
            and all(isinstance(q, str) and 0 < len(q) <= 100 for q in quests)
            and len(set(quests)) == len(quests))


def _valid_time(value) -> bool:
    """None 이나 시간대 없는 ISO 시각만 (로그 시각과 비교하므로, Codex 106 #1)"""
    if value is None:
        return True
    if not isinstance(value, str):
        return False
    try:
        return datetime.fromisoformat(value).tzinfo is None
    except ValueError:
        return False


def _valid(data: dict) -> bool:
    backup = data.get("backup")
    if backup is not None and not (isinstance(backup, dict) and _valid_quests(backup.get("quests"))
                                   and isinstance(backup.get("show_all"), bool)
                                   and isinstance(backup.get("end"), str) and _valid_time(backup["end"])):
        return False
    return (_valid_quests(data.get("quests")) and _valid_time(data.get("picked_at")) and _valid_time(data.get("end_seen"))
            and isinstance(data.get("show_all", False), bool) and isinstance(data.get("cleared", False), bool))


def _time(value):
    return datetime.fromisoformat(value) if value else None


def _load() -> bool:
    """처음 쓸 때 파일을 읽는다. 읽었으면 True. 잠김·권한 등으로 못 읽으면 False (기록을 덮어쓰지 않게, 다음에 다시)"""
    global _data
    if _data is not None:
        return True
    try:
        saved = settings.read_for_reset(PICK_FILE, _valid)  # 손상 + 백업 있음 → None (새로 써도 됨)
    except settings.DataReadError:
        return False
    _data = {**_empty(), **(saved or {})}
    # 프로그램이 꺼진 사이 앞 로그 폴더에서 끝난 레이드 (지금 폴더 것은 감시가 처리)
    if _data["quests"] and _data["picked_at"] and map_watch.raid_ended_since(_time(_data["picked_at"])):
        _data.update(_empty(cleared=True))
        try:
            _save()
        except settings.SaveWriteError:
            pass
    return True


def _save() -> None:
    settings.write_saved(PICK_FILE, _data)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _iso(value) -> str | None:
    return value.isoformat() if value else None


def _follow_log() -> bool:
    """레이드 끝·트랜짓에 맞춰 비우거나 되살린다. 바뀌었으면 True"""
    data = _data
    backup = data["backup"]
    last = map_watch.last_raid_end()
    if backup and map_watch.was_transit(_time(backup["end"])):
        # 트랜짓이었음 → 지난 고르기 되살림. 그사이 새로 고른 것은 뒤에 붙임
        quests = backup["quests"] + [q for q in data["quests"] if q not in backup["quests"]]
        data.update(quests=quests[:MAX_PICKS], show_all=(backup["show_all"] or data["show_all"]) and bool(quests),
                    # 고른 때 = 그 트랜짓 끝(로그 시각): 아는 끝이 없을 때도 그 뒤의 진짜 끝은 풀리게
                    picked_at=backup["end"] if quests else None, end_seen=_iso(last) if quests else None,
                    cleared=False, backup=None)
        return True
    if not (data["quests"] or data["show_all"]) or last is None:
        return False
    if data["end_seen"] is not None:
        if _time(data["end_seen"]) == last:
            return False
    elif data["picked_at"] is None or last <= _time(data["picked_at"]):
        # 고를 때 아는 끝이 없었음 (로그를 아직 못 읽음·새 로그 폴더) → 그때만 고른 시각과 비교
        return False
    old = {"quests": list(data["quests"]), "show_all": data["show_all"], "end": last.isoformat()}
    data.update(_empty(cleared=True))
    data["backup"] = old  # 트랜짓으로 밝혀지면 되살림 (다음 진짜 끝에서 새 backup 으로 바뀜)
    return True


def _snapshot() -> dict:
    if _data is None:
        return {"quests": [], "show_all": False, "cleared": False}
    return {"quests": list(_data["quests"]), "show_all": _data["show_all"], "cleared": _data["cleared"]}


def current() -> dict:
    """화면에 줄 지금 상태: quests(고른 순서), show_all, cleared(레이드가 끝나서 방금 풀림 안내)"""
    with _lock:
        if _load() and _follow_log():
            try:
                _save()
            except settings.SaveWriteError:
                pass  # 메모리에서는 이미 바뀜. 저장 실패는 알림함 SAVE-02 로 보임
        return _snapshot()


def change(*, quest=None, on=None, show_all=None, clear=False) -> dict:
    """quest+on: 고르기/풀기, show_all: 전체 보기 켬/끔, clear: 모두 풀기. 바뀐 상태를 돌려준다"""
    with _lock:
        if not _load():
            raise settings.DataReadError()  # 못 읽은 기록을 덮어쓰지 않음 (화면에는 '저장 못 함')
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
        # 바꾼 때 알던 마지막 끝: 이것과 다른 끝이 생기면 풀림. picked_at 은 끝을 모를 때와 앞 폴더 훑기용
        _data["picked_at"] = _now() if _data["quests"] else None
        _data["end_seen"] = _iso(map_watch.last_raid_end()) if _data["quests"] else None
        _save()
        return _snapshot()

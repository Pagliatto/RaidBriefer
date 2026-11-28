"""사용자가 직접 체크한 내용을 저장하는 파일.

- handed : 제출한 아이템            {"퀘스트id|아이템": true}
- goals  : 해낸 목표 (직접 체크)     {"퀘스트id|목표id": true} (목표 id 없는 직접 적은 목표는 "#순서")
- story  : 단계별 완료 {"단계id!": "done"}, 갈림길 {"챕터id@갈림길id": "선택지id"}
- 저장 위치: user_data/progress.json  (사람마다 다르니 Git에는 올리지 않음)
- 프레스티지를 새로 하면 퀘스트와 스토리가 모두 초기화되므로 체크도 전부 비운다.
"""

import json
import threading
from datetime import datetime
from pathlib import Path

from app import paths, quest_log, settings

USER_DIR = paths.USER_DIR
PROGRESS_FILE = USER_DIR / "progress.json"
KINDS = ("handed", "story", "hidden", "added", "gap_ok", "hideout", "hideout_got", "goals")
# hideout    : 은신처 시설 레벨 {"시설id": 레벨(숫자)} (없으면 기본: 창고 1·약해 보이는 벽 1, 나머지 0)
# hideout_got: 은신처 재료 '모았음' {"시설id|레벨|아이템id": true}
# hidden: 안 할 퀘스트 {"퀘스트id": true}
# gap_ok: '로그가 지워진 것 같아요' 안내를 확인한 퀘스트 {"퀘스트id": true} (다시 안 띄움)
# added : 처음 설정에서 직접 추가한 '진행 중' 퀘스트 {"퀘스트id": true} (로그가 지워졌을 때 메우기)

_AUTO_PRESTIGE = object()

_lock = threading.Lock()  # 동시에 두 곳에서 저장해도 파일이 안 깨지게


def _prestige_mark(prestige=_AUTO_PRESTIGE) -> str:
    # 가장 최근 프레스티지 시각이 바뀌면(새 회차) 체크 기록을 비운다. 기록이 없으면 "none".
    p = quest_log.last_prestige() if prestige is _AUTO_PRESTIGE else prestige
    return p.strftime("%Y-%m-%d %H:%M:%S") if p else "none"


def valid_value(kind: str, value) -> bool:
    if value is None:
        return True
    if kind == "hideout":
        return type(value) is int and 0 <= value <= 10
    if kind == "story":
        return isinstance(value, bool) or (isinstance(value, str) and len(value) <= 100)
    return isinstance(value, bool)


def _valid_saved(data: dict) -> bool:
    if "prestige" in data and not isinstance(data["prestige"], str):
        return False
    return all(kind not in data or (isinstance(data[kind], dict)
               and all(isinstance(key, str) and value is not None and valid_value(kind, value)
                       for key, value in data[kind].items())) for kind in KINDS)


def _read(prestige=_AUTO_PRESTIGE) -> dict:
    data = settings.read_saved(PROGRESS_FILE, _valid_saved)
    # 완전한 조회에서 확인한 더 최근 PvE 프레스티지만 자동 초기화한다.
    # 호출자가 넘긴 기준도 재확인하여 오래된 응답이나 불완전 조회로 저장값을 바꾸지 않는다.
    confirmed, complete = quest_log.prestige_status()
    current = confirmed if prestige is _AUTO_PRESTIGE else prestige
    mark = _prestige_mark(current)
    previous = data.get("prestige")
    if complete and current == confirmed and mark != "none":
        if previous is None:
            data["prestige"] = mark  # 기준이 없던 구버전 기록은 그대로 인계한다.
        else:
            try:
                old = datetime.min if previous == "none" else datetime.fromisoformat(previous)
                newer = old.tzinfo is None and datetime.fromisoformat(mark) > old
            except (ValueError, TypeError):
                newer = False  # 해석할 수 없는 옛 기준도 사용자 기록과 함께 보존한다.
            if newer:
                data = {"prestige": mark}
    for kind in KINDS:
        data.setdefault(kind, {})
    return data


def _write(data: dict) -> None:
    settings.write_saved(PROGRESS_FILE, data)  # 순간 잠금 재시도·실패 알림(SAVE-02)


def get_many(kinds, *, prestige=_AUTO_PRESTIGE) -> dict:
    """한 번 읽은 진행 기록과 프레스티지 기준을 여러 종류가 함께 쓴다."""
    with _lock:
        data = _read(prestige)
        return {kind: data[kind] for kind in kinds}


def get(kind: str) -> dict:
    """체크 목록 하나 (kind = handed 또는 story)"""
    return get_many((kind,))[kind]


def set_check(kind: str, key: str, value) -> dict:
    """값을 저장하고(비어 있거나 False·0이면 지움), 바뀐 목록을 돌려준다.
    value: True/False (체크), 문자열 (스토리 완료·갈림길), 숫자 (은신처 레벨)"""
    return set_checks(kind, {key: value})


def set_checks(kind: str, changes: dict) -> dict:
    """여러 체크를 같은 잠금 안에서 읽고 한 번에 저장한다 (빈 값은 삭제)."""
    with _lock:
        data = _read()
        for key, value in changes.items():
            if value:
                data[kind][key] = value
            else:
                data[kind].pop(key, None)
        _write(data)
        return data[kind]


def reset_all() -> None:
    """전체 초기화: 사용자가 체크한 모든 기록을 비움 (숨김·제출·목표·스토리·직접 추가·은신처 등)"""
    with _lock:
        # 손상 파일은 broken 백업이 있을 때만 새로 쓴다. 잠김·백업 실패는 지금처럼 덮지 않는다.
        # 손상(백업 있음)이면 None → 이전 기록 없이. 정상이면 그대로 읽은 값 (_read 는 로그 조회까지 해서 쓰지 않음)
        old = settings.read_for_reset(PROGRESS_FILE, _valid_saved) or {}
        # 프레스티지 기준은 완전한 조회로 확인한 값만 쓴다. 로그를 못 읽은 때 "none" 을 쓰면
        # 나중에 옛 프레스티지를 '새 회차'로 착각해 초기화 뒤의 체크를 지우게 된다.
        mark = old.get("prestige") if isinstance(old.get("prestige"), str) else None
        try:
            confirmed, complete = quest_log.prestige_status()
        except settings.DataReadError:
            # 설정 파일이 손상돼 로그 위치를 모름 → 불완전 조회와 같게 (기존 기준 보존, 초기화는 계속)
            confirmed, complete = None, False
        if complete and confirmed is not None:
            try:  # 이미 더 최근 기준이 있으면 그대로 둔다 (지워진 로그로 기준이 뒤로 가지 않게)
                keep_old = mark not in (None, "none") and datetime.fromisoformat(mark) >= confirmed
            except (ValueError, TypeError):
                keep_old = False
            if not keep_old:
                mark = _prestige_mark(confirmed)
        fresh = {kind: {} for kind in KINDS}
        if mark is not None:
            fresh["prestige"] = mark
        _write(fresh)

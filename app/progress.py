"""사용자가 직접 체크한 내용을 저장하는 파일.

- handed : 제출한 아이템            {"퀘스트id|아이템": true}
- story  : 단계별 완료 {"단계id!": "done"}, 갈림길 {"챕터id@갈림길id": "선택지id"}
- 저장 위치: user_data/progress.json  (사람마다 다르니 Git에는 올리지 않음)
- 프레스티지를 새로 하면 퀘스트와 스토리가 모두 초기화되므로 체크도 전부 비운다.
"""

import json
import threading
from pathlib import Path

from app import paths, quest_log, settings

USER_DIR = paths.USER_DIR
PROGRESS_FILE = USER_DIR / "progress.json"
KINDS = ("handed", "story", "hidden", "added", "gap_ok", "hideout", "hideout_got")
# hideout    : 은신처 시설 레벨 {"시설id": 레벨(숫자)} (없으면 기본: 창고 1·약해 보이는 벽 1, 나머지 0)
# hideout_got: 은신처 재료 '모았음' {"시설id|레벨|아이템id": true}
# hidden: 안 할 퀘스트 {"퀘스트id": true}
# gap_ok: '로그가 지워진 것 같아요' 안내를 확인한 퀘스트 {"퀘스트id": true} (다시 안 띄움)
# added : 처음 설정에서 직접 추가한 '진행 중' 퀘스트 {"퀘스트id": true} (로그가 지워졌을 때 메우기)

_lock = threading.Lock()  # 동시에 두 곳에서 저장해도 파일이 안 깨지게


def _prestige_mark() -> str:
    # 가장 최근 프레스티지 시각이 바뀌면(새 회차) 체크 기록을 비운다. 기록이 없으면 "none".
    p = quest_log.last_prestige()
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


def _read() -> dict:
    data = settings.read_saved(PROGRESS_FILE, _valid_saved)
    # 프레스티지가 바뀌었으면(새 회차) 체크를 비운다.
    # 단, 로그에서 못 찾았을 때("none": 설치 위치가 틀렸거나 로그가 지워짐)는 지우지 않는다.
    mark = _prestige_mark()
    if mark != "none" and data.get("prestige") not in (None, mark):
        data = {"prestige": mark}
    elif mark != "none":
        data["prestige"] = mark
    for kind in KINDS:
        data.setdefault(kind, {})
    return data


def _write(data: dict) -> None:
    USER_DIR.mkdir(exist_ok=True)
    temp = PROGRESS_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(PROGRESS_FILE)


def get(kind: str) -> dict:
    """체크 목록 하나 (kind = handed 또는 story)"""
    with _lock:
        return _read()[kind]


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
    """전체 초기화: 사용자가 체크한 모든 기록을 비움 (숨김·제출·스토리·직접 추가·은신처 등)"""
    with _lock:
        _read()  # 읽을 수 없는 원본은 명시적 초기화에서도 덮지 않는다.
        _write({"prestige": _prestige_mark(), **{kind: {} for kind in KINDS}})

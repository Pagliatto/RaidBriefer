"""화면에 보여줄 퀘스트 목록을 만드는 파일.

로그(quest_log)로 알아낸 상태 + tarkov.dev 정보(이름, 상인, 맵)를 합친다.
"""

import json
import hashlib
import re
import threading
import time
from datetime import datetime
from pathlib import Path

from app import corrections, i18n, paths, progress, quest_log, tarkov_api

NO_MAP = "@none"        # 계산 중에만 쓰고, 화면에는 아래 둘 중 하나로 바꿔서 보냄
ALL_MAPS = "@any"      # 맵은 상관없지만 레이드에 들어가야 하는 퀘스트
OUT_OF_RAID = "@out"  # 레이드에 안 들어가도 되는 퀘스트 (무기 개조 등)
UNKNOWN_MAP = "@unknown"  # 아직 위치를 확인하지 못한 퀘스트
OVERRIDES_FILE = paths.DATA_DIR / "ko_overrides.json"
HANGUL = re.compile("[가-힣]")  # i18n-ok: 한글이 있는지 검사
_spt_last_started = 0.0
_spt_start_lock = threading.Lock()


def _load_overrides() -> dict:
    """우리가 번역한 퀘스트 목표 (data/ko_overrides.json)"""
    try:
        data = json.loads(OVERRIDES_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return {k: v for k, v in data.items() if not k.startswith("_")}


def _objective_text(key: str, task_tr, overrides: dict) -> dict:
    """목표 문장 고르기.

    1) 게임 공식 한글이 있으면 그것
    2) 없으면 영어 원문 + 우리 번역 (번역할 때 원문과 지금 원문이 같을 때만)
    3) 우리 번역도 없거나 원문이 바뀌었으면 영어 원문만 (needs_translation=True)
    """
    if task_tr.lang != "ko":  # 영어 등: 그 언어 문장 그대로 (우리 한국어 번역은 안 씀)
        return {"text": task_tr.main.get(key) or task_tr.en.get(key) or key}
    official = task_tr.main.get(key)
    if official and HANGUL.search(official):
        return {"text": official}
    original = task_tr.en.get(key) or key
    ours = overrides.get(key)
    if ours and ours.get("en") == original:
        return {"text": ours["ko"], "original": original}
    return {"text": original, "needs_translation": True}


# PvE에서 맵 정리 (지도 구분값 기준)
# - 오염된 공장/연구소는 이벤트 맵이라 뺀다
# - 그라운드 제로 21+ 는 레벨 구분용(PvP/시즌용)이라 PvE에서는 같은 맵
# - 튜토리얼: tarkov.dev가 일반 그라운드 제로를 튜토리얼로 잘못 적은 경우가 있어서
#   (예: 타르코프의 저격수 - 파트 1) 빼지 않고 그라운드 제로로 합친다
# - 야간 공장: 공장과 같은 맵으로 합치고, 야간에만 되는 퀘스트는 달 표시
# 빼는 맵: 이벤트 맵(오염된 공장·연구소), 위키에 없는 맵(연구소 Dark: 실제 플레이 0번, 2026-11-17 위키 확인)
HIDDEN_MAPS = {"infected-factory", "infected-lab", "the-lab-dark"}
MERGED_MAPS = {
    "ground-zero-21": "ground-zero",
    "ground-zero-tutorial": "ground-zero",
    "night-factory": "factory",
}
# 낮·밤이 따로 나뉜 맵은 공장뿐 (위키). 다른 맵은 한 맵 안에서 낮·밤이 바뀜 → ☾ 표시는 야간 공장만
NIGHT_MAPS = {"night-factory"}
# 지도에 그리기엔 너무 큰 영역 (맵 거의 전체이거나 데이터가 이상함) → 서랍 목록에만
MAX_ZONE_METERS = 300

# 레이드 안에서 해야 하는 목표 종류 (무기 개조·아이템 제출·상인 레벨·스킬 등은 레이드 밖)
RAID_OBJECTIVES = {
    "shoot", "extract", "findItem", "findQuestItem", "useItem", "visit", "mark",
    "plantItem", "plantQuestItem", "experience",
}


def _map_names() -> dict:
    """지도 id → (한국어 이름, 야간 맵인가). 합치는 맵은 원래 맵 이름, 빼는 맵은 (None, False)"""
    maps = tarkov_api.load("maps")["maps"]
    tr = tarkov_api.Translator("maps")
    by_key = {m["normalizedName"]: tr(m["name"]) for m in maps.values()}
    names = {}
    for m in maps.values():
        key = m["normalizedName"]
        if key in HIDDEN_MAPS:
            names[m["id"]] = (None, False)
        else:
            names[m["id"]] = (by_key.get(MERGED_MAPS.get(key, key)), key in NIGHT_MAPS)
    return names


def _trader_names() -> dict:
    """상인 id → (한국어 이름, 영어 구분값 예: prapor). 구분값은 화면 색상용."""
    traders = tarkov_api.load("traders")
    tr = tarkov_api.Translator("traders")
    return {t["id"]: (tr(t["name"]), t.get("normalizedName", "")) for t in traders.values()}


def _quest_maps(task: dict, map_names: dict) -> tuple[list[str], list[str]]:
    """퀘스트에 관련된 맵 이름들 (퀘스트 자체 맵 + 목표들의 맵).

    돌려주는 값: (맵 이름들, 야간에만 되는 맵 이름들)
    예) 야간 공장만 적혀 있으면 → (["공장"], ["공장"])  → 화면에 달 표시
    """
    ids = []
    if task.get("map"):
        ids.append(task["map"])
    for obj in task.get("objectives", []):
        ids.extend(obj.get("maps") or [])
    names, day, night = [], set(), set()
    for map_id in ids:
        name, is_night = map_names.get(map_id, (None, False))
        if not name:
            continue
        (night if is_night else day).add(name)
        if name not in names:
            names.append(name)
    night_only = [n for n in names if n in night and n not in day]
    return (names or [NO_MAP]), night_only


def _flat(value) -> list:
    """[[a, b], [c]] 처럼 겹친 목록을 [a, b, c]로 편다.
    {"id": ..., "name": ...} 모양이면 id만 꺼낸다."""
    if isinstance(value, list):
        out = []
        for v in value:
            out.extend(_flat(v))
        return out
    if isinstance(value, dict):
        return [value["id"]] if value.get("id") else []
    return [value] if value else []


def _item_names(ids, item_tr) -> str:
    """아이템 id 목록 → '이름 / 이름' (너무 많으면 '이름 외 N종')."""
    names = []
    for item_id in _flat(ids):
        name = item_tr(f"{item_id} Name")
        if name != f"{item_id} Name" and name not in names:
            names.append(name)
    if not names:
        return ""
    if len(names) <= 3:
        return " / ".join(names)
    return i18n.t("server.andMore", lang=item_tr.lang, first=names[0], second=names[1], n=len(names) - 2)


def _objectives_and_prep(task: dict, task_tr, item_tr, quest_items: dict, overrides: dict, map_names: dict | None = None):
    """퀘스트 목표 문장들과, 가기 전에 챙길 준비물 목록을 만든다.

    준비물 종류
    - 가져가기 : 설치할 물건, 마커, 사용할 아이템
    - 무기     : 지정된 무기로 처치, 무기 조립
    - 착용     : 입고 있어야 하는 장비
    - 열쇠     : 목표 장소에 필요한 열쇠
    - 제출     : 상인에게 낼 아이템 (fir = 레이드에서 직접 찾은 것만 인정)
    """
    objectives = []
    prep = {}  # (종류, 이름, 인레이드) → 개수 합계. 같은 물건이 여러 목표에 나오면 개수를 더한다.
    keys = {}  # 정렬한 열쇠 id 묶음 → 원래 순서의 대체 열쇠 목록
    key_maps = {}  # 열쇠 id 묶음 → 필요한 맵 이름들 (None = 맵 정보 없음 → 어느 맵에서나 보여줌)

    prep_text = {}
    legacy_names = {}
    legacy_tr = [tarkov_api.Translator("items", lang=lang) for lang in ("ko", "en")]
    item_ids = {}  # (종류, 이름, 인레이드) → 아이템 id들 (지도에 바닥 생성 위치를 찍을 때 씀)

    def add(kind, text, count=None, fir=False, ids=None):
        if text:
            identity = tuple(sorted(_flat(ids))) if kind == "제출" and ids else text
            k = (kind, identity, fir)
            prep_text[k] = text
            prep[k] = prep.get(k, 0) + (count or 1)
            if ids:
                item_ids.setdefault(k, [])
                item_ids[k] += [i for i in _flat(ids) if i not in item_ids[k]]
                legacy_names.setdefault(k, set()).update(_item_names(ids, tr) for tr in legacy_tr)

    def map_display_names(map_ids):
        names = []
        for mid in map_ids or []:
            mid = mid.get("id") if isinstance(mid, dict) else mid
            name = (map_names or {}).get(mid, (None, False))[0]
            if name and name not in names:
                names.append(name)
        return names

    def key_info(key_id):
        name = _item_names([key_id], item_tr)
        return {"id": key_id, "name": name, "wiki": tarkov_api.key_wiki(key_id)} if name else None

    def add_keys(ids, map_ids=None):
        group_ids = list(dict.fromkeys(_flat(ids)))
        group = tuple(sorted(group_ids))
        if not group:
            return
        maps = map_display_names(map_ids)
        if group not in keys:
            members = [info for key_id in group_ids if (info := key_info(key_id))]
            if not members:
                return
            keys[group] = members
            key_maps[group] = maps or None
        elif key_maps[group] is not None:
            # 같은 묶음의 맵을 합침 (맵 정보 없는 곳에서도 필요하면 어디서나)
            key_maps[group] = (key_maps[group] + [m for m in maps if m not in key_maps[group]]) if maps else None

    for o in task.get("objectives", []):
        objective = _objective_text(o["description"], task_tr, overrides)
        objective["optional"] = o.get("optional", False)
        objectives.append(objective)
        kind = o["type"]
        count = o.get("count") or 1

        if kind == "plantItem":
            add("가져가기", _item_names(o.get("items"), item_tr), count)
        elif kind == "mark":
            add("가져가기", _item_names([o.get("markerItem")], item_tr), count)
        elif kind == "useItem":
            add("가져가기", _item_names(o.get("useAny"), item_tr), count)
        elif kind == "shoot":
            # 무기·착용은 개수 의미가 없어서 1개로 센다
            add("무기", _item_names(o.get("usingWeapon"), item_tr))
            add("무기", _item_names(o.get("usingWeaponMods"), item_tr))
            add("착용", _item_names(o.get("wearing"), item_tr))
        elif kind == "buildWeapon":
            add("무기", _item_names([o.get("item")], item_tr))
        elif kind == "giveItem":
            add("제출", _item_names(o.get("items"), item_tr), count, o.get("foundInRaid", False), o.get("items"))
        elif kind == "plantQuestItem":
            qi = quest_items.get(o.get("questItem"), {})
            add("가져가기", task_tr(qi.get("name", "")) if qi else "", count)

        for group in o.get("requiredKeys") or []:
            add_keys(group, o.get("maps"))

    # neededKeys 는 '이 맵에서 필요한 열쇠 전부'(대체 묶음이 아님) → 하나씩. 목표 쪽에 이미 나온 열쇠는 건너뜀
    covered = {key_id for group in keys for key_id in group}
    for needed in task.get("neededKeys") or []:
        for key_id in _flat(needed.get("keys")):
            if key_id not in covered:
                add_keys([key_id], [needed.get("map")] if needed.get("map") else None)

    # 화면에 보여줄 순서: 열쇠 → 가져가기 → 무기 → 착용 → 제출
    order = {"가져가기": 1, "무기": 2, "착용": 3, "제출": 4}
    # 열쇠에는 필요한 맵(maps)을 붙임 → 홈 '챙길 것'에서 고른 맵 열쇠만 보여줌
    result = [{"kind": "열쇠", "text": " / ".join(k["name"] for k in members),
               "keys": members, "any_of": len(members) > 1, "maps": key_maps[group] or [],
               "count": None, "fir": False}
              for group, members in keys.items()]
    for k, total in sorted(prep.items(), key=lambda x: order[x[0][0]]):
        kind, _, fir = k
        text = prep_text[k]
        entry = {"kind": kind, "text": text, "count": total if total > 1 else None, "fir": fir}
        if k in item_ids:
            entry["ids"] = item_ids[k]
            if kind == "제출":
                identity = ",".join(sorted(entry["ids"]))
                if len(identity) > 200:
                    identity = hashlib.sha256(identity.encode("utf-8")).hexdigest()
                entry["hand_id"] = "item:" + identity + (":fir" if fir else ":any")
                entry["legacy_texts"] = sorted(legacy_names[k] | {text})
                key_items = [info for item_id in entry["ids"]
                             if tarkov_api.is_key(item_id) and (info := key_info(item_id))]
                if key_items:
                    entry["keys"] = key_items
        result.append(entry)
    return objectives, result


def _xz(position: dict) -> list[float]:
    """게임 좌표 → [x, z] (지도는 위에서 내려다보는 평면)"""
    return [round(position.get("x", 0), 2), round(position.get("z", 0), 2)]


def _xzy(position: dict) -> list[float]:
    """게임 좌표 → [x, z, 높이 y] (핀: 높이로 몇 층인지 판단)"""
    return _xz(position) + [round(position.get("y", 0), 2)]


def _extract_zones() -> dict:
    """(맵 id, 탈출구 이름) → 탈출구·Transit 영역. build마다 한 번 만든다."""
    lookup = {}
    for m in tarkov_api.load("maps")["maps"].values():
        for exit in m.get("extracts") or []:
            if exit.get("name"):
                lookup.setdefault((m["id"], exit["name"]), []).append({**exit, "map": m["id"]})
        # dev 에 없는 탈출구 (위키 기준 보완, 예: 등대 ' V-Ex_light')
        for exit in corrections.manual_extracts(m.get("normalizedName", ""), m.get("extracts")):
            lookup.setdefault((m["id"], exit["name"]), []).append({**exit, "map": m["id"]})
        for transit in m.get("transits") or []:
            description = transit.get("description") or ""
            if description.endswith("_DESC"):
                key = (m["id"], description.removesuffix("_DESC"))
                lookup.setdefault(key, []).append({**transit, "map": m["id"]})
    return lookup


def _where(objective: dict, map_names: dict, extract_zones: dict | None = None) -> list[dict]:
    """목표의 지도 위치들. [{"map": "세관", "points": [[x, z]], "outline": [[x, z], ...] 또는 없음}]
    - zones           : 가야 할 곳·설치·표시할 곳 (영역 모양 outline 포함)
    - possibleLocations: 퀘스트 아이템이 있을 수 있는 곳들
    - exitName        : 지정된 맵의 탈출구·Transit"""
    result = []
    zones = list(objective.get("zones") or [])
    if objective.get("type") == "extract" and objective.get("exitName"):
        exit_name = objective["exitName"].strip()
        for map_id in objective.get("maps") or []:
            zones.extend((extract_zones or {}).get((map_id, exit_name), []))
    for zone in zones:
        name = map_names.get(zone.get("map"), (None, False))[0]
        if not (name and zone.get("position")):
            continue
        outline = [_xz(p) for p in zone.get("outline") or []]
        if outline:
            xs = [p[0] for p in outline]
            zs = [p[1] for p in outline]
            if max(max(xs) - min(xs), max(zs) - min(zs)) > MAX_ZONE_METERS:
                continue  # 맵 거의 전체 같은 영역은 지도에 안 그림
        spot = {"map": name, "points": [_xzy(zone["position"])]}
        if outline:
            spot["outline"] = outline
            # 처치 구역은 '이 근처에서 싸움' 이라 화면에서 점선으로 옅게
            spot["area"] = objective.get("type") == "shoot"
        result.append(spot)
    for loc in objective.get("possibleLocations") or []:
        name = map_names.get(loc.get("map"), (None, False))[0]
        if name and loc.get("positions"):
            result.append({"map": name, "points": [_xzy(p) for p in loc["positions"]]})
    # tarkov.dev 데이터에 같은 영역이 두 번 들어 있는 경우가 있어서(30개 퀘스트, 59건) 하나만 남김
    unique, seen = [], set()
    for spot in result:
        key = (spot["map"], str(spot["points"]), str(spot.get("outline")))
        if key not in seen:
            seen.add(key)
            unique.append(spot)
    result = unique
    return result


def infer_completed(status: dict, tasks: dict) -> dict:
    """로그에 기록이 없지만 '끝낸 게 확실한' 퀘스트를 찾는다.

    어떤 퀘스트를 받았다면(진행 중·완료·실패 모두), 그 퀘스트의 선행 퀘스트는 이미 끝낸 것.
    로그가 지워졌을 때(게임 재설치·포맷) 새 로그만으로 지난 진행을 메우는 용도.
    - tarkov.dev 선행 조건 중 '완료해야 함'(["complete"])만 사용
    - 이름표의 same_as(옛 id 정보를 빌려 씀)는 선행 조건이 바뀌었을 수 있어서 사용 안 함
    돌려주는 값: 추정한 퀘스트 id → 그렇게 판단한 근거 퀘스트 id
    """
    inferred = {}
    stack = list(status)
    while stack:
        quest_id = stack.pop()
        for req in (tasks.get(quest_id) or {}).get("taskRequirements") or []:
            prereq = req.get("task")
            if req.get("status") == ["complete"] and prereq not in status and prereq not in inferred:
                inferred[prereq] = quest_id
                stack.append(prereq)
    return inferred


def _has_manual_objectives(entry: dict) -> bool:
    """빈 목표 목록도 명시적인 수정값으로 인정한다."""
    return any(entry.get(key) is not None for key in ("objectives", "objective", "objective_en"))


def _manual_trader(entry: dict, trader_names: dict) -> tuple[str, str]:
    """이름표의 한국어 상인 이름을 화면 언어로 바꾼다."""
    ko_traders = tarkov_api.Translator("traders", lang="ko")
    trader_id = next((t["id"] for t in tarkov_api.load("traders").values()
                      if ko_traders(t["name"]) == entry["trader"]), None)
    return trader_names.get(trader_id, (entry["trader"], ""))


def hidden_quest(quest_id: str, tasks: dict, manual: dict) -> bool:
    """dev·보완에 없는 이벤트와 이름이 빈 스토리 단계만 숨긴다."""
    return (quest_id not in tasks and quest_id not in manual
            and (tarkov_api.spt_blank_name(quest_id) or tarkov_api.spt_event_quest(quest_id)))


def catalog() -> list[dict]:
    """처음 설정에서 고를 수 있는 전체 퀘스트 (이름·상인·레벨). 로그로 이미 아는 것은 화면에서 거름"""
    tarkov_api.update_all()
    tasks, tr = tarkov_api.get_tasks()
    manual = quest_log.load_manual_quests()
    trader_names = _trader_names()
    result = []
    for quest_id, task in tasks.items():
        m = manual.get(quest_id, {})
        game = m if m.get("from") == "game" else {}
        trader, trader_key = trader_names.get(task.get("trader"), ("?", ""))
        if game.get("trader"):
            trader, trader_key = _manual_trader(game, trader_names)
        name = quest_log.quest_name(quest_id, tasks, tr, manual)
        english = game.get("name_en") or tr.en.get(task["name"], "")
        result.append({
            "id": quest_id,
            "name": name,
            "source": "manual" if _has_manual_objectives(game) else "dev",
            "en": english if english != name else "",  # 영어 이름으로도 찾을 수 있게
            "trader": trader,
            "trader_key": trader_key,
            "level": game["level"] if game.get("level") is not None else task.get("minPlayerLevel"),
        })
    result.sort(key=lambda q: (q["level"] or 0, q["name"]))
    return result


def build() -> dict:
    """화면용 퀘스트 정보를 만든다."""
    global _spt_last_started
    with _spt_start_lock:
        now = time.time()
        if now - _spt_last_started >= tarkov_api.SPT_RETRY_SECONDS:
            try:
                threading.Thread(target=tarkov_api.update_spt_names, daemon=True).start()
                _spt_last_started = now
            except RuntimeError:
                pass  # 이름 보완 실패가 퀘스트 목록을 막지 않게 한다.
    message = tarkov_api.update_all()
    tasks, tr = tarkov_api.get_tasks()
    manual = quest_log.load_manual_quests()
    map_names = _map_names()
    extract_zones = _extract_zones()
    trader_names = _trader_names()
    item_tr = tarkov_api.Translator("items")
    quest_items = tarkov_api.load("tasks").get("questItems", {})
    overrides = _load_overrides()

    groups = {"진행 중": [], "완료": [], "실패": []}
    prestige = quest_log.last_prestige()
    events = quest_log.read_quest_events(since=prestige or datetime.min)
    saved = progress.get_many(("added", "gap_ok"), prestige=prestige)
    status = quest_log.status_from_events(events)
    # 로그 일부가 지워졌다는 단서 (직접 추가했거나 이미 확인한 것은 뺌)
    known_ok = set(saved["added"]) | set(saved["gap_ok"])
    log_gap = sorted(quest_log.missing_starts(events) - known_ok)
    # 처음 설정에서 직접 추가한 '진행 중' 퀘스트 (로그에 기록이 생기면 로그가 이김)
    added = [q for q in saved["added"] if q not in status]
    for quest_id in added:
        status[quest_id] = {"status": "진행 중", "time": None}
    # 로그에 없지만 선행 퀘스트라서 끝낸 게 확실한 것 → 완료로 추가 ("자동 추정" 표시)
    inferred = infer_completed(status, tasks)
    for quest_id in inferred:
        status[quest_id] = {"status": "완료", "time": None}

    # 수동 데이터의 same_as 연결도 포함해, dev·수동 퀘스트는 숨기지 않는다.
    hidden_quests = {
        quest_id for quest_id in set(status) | set(log_gap)
        if hidden_quest(quest_id, tasks, manual)
    }
    log_gap = [quest_id for quest_id in log_gap if quest_id not in hidden_quests]

    for quest_id, info in status.items():
        if quest_id in hidden_quests:
            continue
        # tarkov.dev 정보 찾기 (옛 id로 있는 경우 same_as 사용)
        m = manual.get(quest_id, {})
        task = tasks.get(quest_id) or tasks.get(m.get("same_as"))
        # 게임 직접 확인값만 dev를 덮는다. 위키·블로그는 dev가 없을 때만 사용한다.
        details = m if m.get("from") == "game" or not task else {}
        if _has_manual_objectives(details) and details.get("from") == "game":
            source = "manual"
        elif task is not None:
            source = "dev"
        elif _has_manual_objectives(details) and details.get("from") in {"wiki", "blog"}:
            source = details["from"]
        elif tarkov_api.spt_quest_name(quest_id, tr.lang):
            source = "spt"
        else:
            source = "unknown"
        entry = {
            "id": quest_id,
            "name": quest_log.quest_name(quest_id, tasks, tr, manual),
            # 정렬용. 추정한 것은 기록 시각이 없어서 맨 아래로
            "time": info["time"].strftime("%m-%d %H:%M") if info["time"] else "",
            "source": source,
        }
        if tr.lang == "ko":  # 한국어 화면: 영어 이름도 같이 (제목 옆에 작게). 이름이 원래 영어면 생략
            name_en = quest_log.quest_name_en(quest_id, tasks, tr, manual)
            if name_en and name_en != entry["name"]:
                entry["name_en"] = name_en
        if quest_id in added:
            entry["added"] = True
        if quest_id in inferred:
            entry["inferred"] = True
            entry["inferred_from"] = quest_log.quest_name(inferred[quest_id], tasks, tr, manual)
        if task:
            entry["trader"], entry["trader_key"] = trader_names.get(task.get("trader"), ("?", ""))
            entry["maps"], entry["night_maps"] = _quest_maps(task, map_names)
            entry["level"] = task.get("minPlayerLevel")
            entry["wiki"] = task.get("wikiLink")
            entry["objectives"], entry["prep"] = _objectives_and_prep(
                task, tr, item_tr, quest_items, overrides, map_names)
            # 목표마다 지도 위치 (목표 문장과 같은 순서)
            for objective, raw in zip(entry["objectives"], task.get("objectives", [])):
                where = _where(raw, map_names, extract_zones)
                if where:
                    objective["where"] = where
            # 레이드 안에서 하는 목표가 있나 (맵 무관 퀘스트를 고른 맵에 같이 보여줄지 판단)
            entry["raid"] = any(o["type"] in RAID_OBJECTIVES for o in task.get("objectives", []))
        else:
            location = tarkov_api.spt_quest_location(quest_id)
            if location in map_names:
                entry["maps"], entry["night_maps"] = _quest_maps({"map": location}, map_names)
            else:
                entry["maps"] = [NO_MAP if location == "any" else UNKNOWN_MAP]
                entry["night_maps"] = []
            entry["level"] = None
            entry["wiki"] = None
            entry["objectives"], entry["prep"] = [], []
            entry["raid"] = True

        # 상인: 게임 → dev → 위키·블로그 → 로그. dev 항목이 있으면 위키·블로그는 제외.
        if details.get("trader"):
            entry["trader"], entry["trader_key"] = _manual_trader(details, trader_names)
        elif not task or not task.get("trader"):
            entry["trader"], entry["trader_key"] = trader_names.get(info.get("trader_id"), ("?", ""))

        # 맵: 게임 → dev → SPT → 위키·블로그 → 미확인. 빈 게임 목록도 직접 지정값이다.
        if (details.get("from") == "game" and details.get("maps") is not None
                or details.get("maps") and entry["maps"] == [UNKNOWN_MAP]):
            keyed = {mm["normalizedName"]: mm["id"] for mm in tarkov_api.load("maps")["maps"].values()}
            entry["maps"], entry["night_maps"] = _quest_maps(
                {"objectives": [{"maps": [keyed.get(k) for k in details["maps"]]}]}, map_names)

        # 목표·준비물·레벨·링크는 각각 있는 필드만 덮어쓴다 (빈 목록 / 레벨 0 포함).
        english = i18n.current_lang() != "ko"
        if details.get("level") is not None:
            entry["level"] = details["level"]
        if details.get("link") is not None:
            entry["wiki"] = details["link"]
        if _has_manual_objectives(details):
            if details.get("objectives") is not None:
                entry["objectives"] = [
                    {"optional": o.get("optional", False), "text": o.get("original") or o["text"]} if english
                    else {"optional": False, **o}
                    for o in details["objectives"]]
            else:
                text = (details.get("objective_en") if english else None) or details.get("objective")
                entry["objectives"] = [{"text": text, "optional": False}] if text else []
        if details.get("prep") is not None:
            entry["prep"] = [{"count": None, "fir": False, **p, "text": (p.get("text_en") if english else None) or p["text"]}
                             for p in details["prep"]]
        # 직접 보완한 준비물은 문구가 아닌 고정 항목 위치로 구분한다.
        for index, prep in enumerate(entry["prep"]):
            if prep["kind"] == "제출" and "hand_id" not in prep:
                prep["hand_id"] = "manual:" + str(index)
                original = (details.get("prep") or [])[index]
                prep["legacy_texts"] = list(dict.fromkeys(filter(None, [original.get("text"), original.get("text_en")])))
        # raid는 기존 사용자 확인값 우선 규칙을 유지한다.
        if "raid" in m:
            entry["raid"] = m["raid"]
        # 맵이 없는 퀘스트: 레이드에서 하는 거면 "모든 맵", 아니면 "레이드 밖"
        if entry["maps"] == [NO_MAP]:
            entry["maps"] = [ALL_MAPS if entry.get("raid") else OUT_OF_RAID]
        groups[info["status"]].append(entry)

    # 최근에 받은/끝낸 것이 위로
    for items in groups.values():
        items.sort(key=lambda e: e["time"], reverse=True)

    # 번역이 필요한 목표 개수 (원문이 바뀌었거나 새로 생긴 것)
    needs = sum(1 for items in groups.values() for q in items
                for o in q["objectives"] if o.get("needs_translation"))

    return {
        "data_message": message,
        "needs_translation": needs,
        # 가장 최근 프레스티지 (없으면 None → 화면에 "전체 로그 기준")
        "prestige_since": prestige.strftime("%Y-%m-%d %H:%M:%S") if prestige else None,
        # 시작 기록 없이 끝난 퀘스트 id들 → 화면에 '로그가 지워진 것 같아요' 안내
        "log_gap": log_gap,
        "in_progress": groups["진행 중"],
        "completed": groups["완료"],
        "failed": groups["실패"],
    }

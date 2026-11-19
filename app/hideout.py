"""은신처 정보 (은신처 탭·처음 설정 3단계·먹을 것용).

- 데이터: tarkov.dev 은신처 (json.tarkov.dev/pve/hideout + 한국어 이름)
- 시설 레벨과 재료 '모았음' 체크는 사용자가 직접 (건설 기록은 게임 로그에 남지 않음)
  저장: user_data/progress.json 의 hideout / hideout_got (프레스티지 때 같이 비워짐)
- 합계는 '다음 레벨만' (사용자 결정)
"""

from app import i18n, progress, settings, tarkov_api


def _skill_name(skill: str) -> str:
    """스킬 이름 (은신처 조건에 나오는 것만 사전 skills 에 있음, 없으면 영어 그대로)"""
    name = i18n.t(f"skills.{skill}")
    return skill if name == f"skills.{skill}" else name



# 게임 에디션에 따라 처음부터 지어져 있는 레벨 (공식 위키 Hideout, 2026-11-17 확인)
#   창고: Standard 1 / Left Behind 2 / Prepare for Escape 3 / Edge of Darkness·The Unheard 4
#   광신도 제단: The Unheard 는 1레벨이 지어져 있음
EDITION_LEVELS = {
    "stash": {"standard": 1, "left_behind": 2, "prepare_for_escape": 3, "edge_of_darkness": 4, "unheard": 4},
    "cultist-circle": {"unheard": 1},
}


def default_level(station: dict, edition: str = "standard") -> int:
    """처음부터 지어져 있는 레벨. 프레스티지로 초기화돼도 이 상태로 돌아감.
    - 재료·건설 시간이 0인 레벨이 1부터 이어지는 만큼 (예: 약해 보이는 벽 1)
    - 에디션에 따라 더 높음 (창고, 광신도 제단)"""
    level = 0
    for lv in sorted(station.get("levels", []), key=lambda x: x["level"]):
        if lv["level"] == level + 1 and not lv.get("itemRequirements") and not lv.get("constructionTime"):
            level = lv["level"]
        else:
            break
    by_edition = EDITION_LEVELS.get(station.get("normalizedName"), {})
    return max(level, by_edition.get(edition, 0))


def build() -> dict:
    tarkov_api.update_all()
    stations = tarkov_api.load("hideout")
    names = tarkov_api.Translator("hideout")
    items = tarkov_api.Translator("items")
    traders = {t["id"]: tarkov_api.Translator("traders")(t["name"]) for t in tarkov_api.load("traders").values()}
    saved_levels = progress.get("hideout")
    got = progress.get("hideout_got")

    def item_name(item_id: str) -> str:
        name = items(f"{item_id} Name")
        return item_id if name.endswith(" Name") else name

    edition = settings.load().get("edition", "standard")
    include_locked = bool(settings.load().get("hideout_all"))  # 조건 남은 시설 재료도 합칠지
    defaults = {sid: default_level(s, edition) for sid, s in stations.items()}
    # 지금 레벨: 저장값이 있으면 그것, 없으면 기본. 에디션 기본보다 낮을 수는 없음
    levels = {sid: max(saved_levels.get(sid, 0), defaults[sid]) for sid, s in stations.items()}

    result = []
    totals = {}  # 다음 레벨 재료 합계 (모았음 체크한 건 빼고): 아이템id → {name, count, fir}
    for sid, s in stations.items():
        current = levels[sid]
        by_level = {lv["level"]: lv for lv in s.get("levels", [])}
        max_level = max(by_level) if by_level else 0
        entry = {
            "id": sid,
            "key": s.get("normalizedName"),
            "name": names(s["name"]),
            "image": s.get("imageLink"),
            "level": current,
            "default_level": defaults[sid],
            "max_level": max_level,
            "next": None,
        }
        nxt = by_level.get(current + 1)
        if nxt:
            need_items = []
            for req in nxt.get("itemRequirements") or []:
                key = f"{sid}|{current + 1}|{req['item']}"
                fir = bool((req.get("attributes") or {}).get("foundInRaid"))
                done = bool(got.get(key))
                need_items.append({
                    "id": req["item"],
                    "name": item_name(req["item"]),
                    "count": req.get("count") or 1,
                    "fir": fir,
                    "got": done,
                    "key": key,
                })
            need_stations = []
            for req in nxt.get("stationLevelRequirements") or []:
                other = stations.get(req["station"], {})
                need_stations.append({
                    "id": req["station"],
                    "name": names(other.get("name", "")),
                    "level": req["level"],
                    "ok": levels.get(req["station"], 0) >= req["level"],
                })
            entry["next"] = {
                "level": current + 1,
                "items": need_items,
                "stations": need_stations,
                # 상인 레벨·스킬은 로그로 알 수 없어서 보여주기만 (지을 수 있는지 판단에서는 뺌)
                "traders": [{"name": traders.get(r.get("trader"), "?"), "level": r.get("value")}
                            for r in nxt.get("traderRequirements") or []],
                "skills": [{"name": _skill_name(r["skill"]), "level": r["level"]}
                           for r in nxt.get("skillRequirements") or []],
                "seconds": nxt.get("constructionTime") or 0,
            }
            entry["buildable"] = all(x["ok"] for x in need_stations)
        result.append(entry)

    # 다음 레벨 재료 합계 ('모았음' 체크한 건 빼고).
    # 스위치가 꺼져 있으면 지금 지을 수 있는 시설만, 켜져 있으면 조건이 남은 시설까지 (사용자 결정)
    for entry in result:
        if not entry["next"] or not (include_locked or entry.get("buildable")):
            continue
        for item in entry["next"]["items"]:
            if item["got"]:
                continue
            t = totals.setdefault(item["id"], {"id": item["id"], "name": item["name"], "count": 0, "fir": False, "from": []})
            t["count"] += item["count"]
            t["fir"] = t["fir"] or item["fir"]
            t["from"].append(entry["name"])

    # 보기 좋은 순서: 지금 지을 수 있는 것 → 조건이 남은 것 → 다 지은 것, 그 안에서는 이름순
    def order(e):
        if not e["next"]:
            return (2, e["name"])
        return (0 if e.get("buildable") else 1, e["name"])

    result.sort(key=order)
    return {
        "edition": edition,
        "include_locked": include_locked,
        "stations": result,
        "totals": sorted(totals.values(), key=lambda x: (-x["count"], x["name"])),
    }

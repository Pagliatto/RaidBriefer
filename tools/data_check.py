"""보완 데이터와 가이드를 검사한다. --guides-only는 캐시·로그 없이 검사. 다운로드·저장 없음."""

import json
import math
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def check_guides() -> int:
    """가이드만 읽어 검사한다. 캐시·게임 로그·사용자 설정은 필요하지 않다."""
    folder = ROOT / "data" / "guides"
    if not folder.exists():
        return 0
    problems = []
    try:
        files = sorted(folder.glob("*.json"))
    except OSError as error:
        print(f"\n가이드 데이터\n  data/guides: {error}")
        return 1
    if not files:
        return 0

    def invalid_constant(value):
        raise ValueError(f"JSON에서 허용하지 않는 값: {value}")

    def read_json(path):
        try:
            return json.loads(path.read_text(encoding="utf-8"), parse_constant=invalid_constant)
        except (OSError, ValueError) as error:
            problems.append(f"{path.relative_to(ROOT)}: JSON 읽기 실패 ({error})")
            return None

    story_ids = set()
    for path in sorted((ROOT / "data" / "story").glob("*.json")):
        if path.name == "places.json":
            continue  # 스토리 장소 핀 파일 (챕터가 아님, story_check.py 가 검사)
        story = read_json(path)
        if not isinstance(story, dict) or not isinstance(story.get("sections"), list):
            problems.append(f"{path.relative_to(ROOT)}: sections 목록 없음")
            continue
        for section in story["sections"]:
            if not isinstance(section, dict):
                continue
            steps = section.get("steps", [])
            if isinstance(steps, list):
                story_ids.update(step["id"] for step in steps
                                 if isinstance(step, dict) and isinstance(step.get("id"), str))

    hangul = re.compile(r"[\u1100-\u11ff\u3130-\u318f\ua960-\ua97f\uac00-\ud7ff\uffa0-\uffdc]")
    kinds = {"key_spawn", "panel", "toolset", "explosive", "door", "building", "extract", "start", "lock"}
    certainties = {"wiki", "user", "unconfirmed"}

    for path in files:
        prefix = str(path.relative_to(ROOT))

        def problem(where, message):
            problems.append(f"{prefix}/{where}: {message}")

        def text_pair(value, where, keys=("ko", "en")):
            if not isinstance(value, dict):
                problem(where, "객체가 아님")
                return
            for key in keys:
                text = value.get(key)
                if not isinstance(text, str) or not text.strip():
                    problem(f"{where}/{key}", "비어 있지 않은 문자열 필요")
                elif key == keys[1] and hangul.search(text):
                    problem(f"{where}/{key}", "영어에 한글 포함")

        guide = read_json(path)
        if not isinstance(guide, dict):
            problem("root", "객체가 아님")
            continue
        required = {"map": str, "checked": str, "sources": dict, "prep": list,
                    "steps": list, "points": dict, "warnings": list}
        for key, kind in required.items():
            if key not in guide or not isinstance(guide[key], kind):
                problem(key, f"필수 필드 또는 형식 오류 ({kind.__name__})")
        if not isinstance(guide.get("map"), str) or not guide["map"].strip():
            problem("map", "비어 있지 않은 맵 이름 필요")
        checked = guide.get("checked")
        try:
            if not isinstance(checked, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", checked):
                raise ValueError
            datetime.strptime(checked, "%Y-%m-%d")
        except ValueError:
            problem("checked", "실제 날짜 YYYY-MM-DD 필요")

        source_entries = guide.get("sources")
        source_entries = source_entries if isinstance(source_entries, dict) else {}
        for source_id, entry in source_entries.items():
            if not source_id.strip() or not isinstance(entry, dict):
                problem(f"sources/{source_id}", "출처 id 또는 객체 오류")
                continue
            for field in ("from", "link", "section"):
                if not isinstance(entry.get(field), str) or not entry[field].strip():
                    problem(f"sources/{source_id}/{field}", "비어 있지 않은 문자열 필요")

        def reference(value, where, required=False):
            if required or "source" in value:
                source_id = value.get("source")
                if not isinstance(source_id, str) or source_id not in source_entries:
                    problem(f"{where}/source", "sources에 없는 출처 id")
            if required or "certainty" in value:
                certainty = value.get("certainty")
                if not isinstance(certainty, str) or certainty not in certainties:
                    problem(f"{where}/certainty", "wiki/user/unconfirmed 중 하나 필요")

        def warnings_list(value, where):
            if not isinstance(value, list):
                problem(where, "경고 목록 필요")
                return
            for index, warning in enumerate(value):
                location = f"{where}/{index}"
                text_pair(warning, location)
                if isinstance(warning, dict):
                    reference(warning, location)

        point_entries = guide.get("points")
        point_entries = point_entries if isinstance(point_entries, dict) else {}
        for point_id, point in point_entries.items():
            where = f"points/{point_id}"
            text_pair(point, where, ("label_ko", "label_en"))
            if not point_id.strip():
                problem(where, "빈 위치 id")
            if not isinstance(point, dict):
                continue
            for axis in ("x", "y", "z"):
                number = point.get(axis)
                if type(number) not in (int, float) or (isinstance(number, float) and not math.isfinite(number)):
                    problem(f"{where}/{axis}", "유한한 숫자 좌표 필요")
            kind = point.get("kind")
            if not isinstance(kind, str) or kind not in kinds:
                problem(f"{where}/kind", "알 수 없는 위치 종류")
            if "candidate" in point and not isinstance(point["candidate"], bool):
                problem(f"{where}/candidate", "true/false 필요")
            reference(point, where)

        row_ids = set()
        for section in ("prep", "steps"):
            rows = guide.get(section)
            if not isinstance(rows, list):
                continue
            for index, row in enumerate(rows):
                where = f"{section}/{index}"
                text_pair(row, where)
                if not isinstance(row, dict):
                    continue
                row_id = row.get("id")
                if not isinstance(row_id, str) or not row_id.strip() or row_id in row_ids:
                    problem(f"{where}/id", "단계 id 누락 또는 중복")
                else:
                    row_ids.add(row_id)
                reference(row, where, required=True)
                if section == "steps" or "story_step" in row:
                    story_id = row.get("story_step")
                    if "story_step" not in row or (story_id is not None and
                            (not isinstance(story_id, str) or story_id not in story_ids)):
                        problem(f"{where}/story_step", "기존 스토리 단계 id 또는 null 필요")
                if section == "steps" or "points" in row:
                    refs = row.get("points")
                    if not isinstance(refs, list):
                        problem(f"{where}/points", "위치 id 목록 필요")
                    else:
                        for ref in refs:
                            if not isinstance(ref, str) or ref not in point_entries:
                                problem(f"{where}/points", f"없는 위치 id: {ref!r}")
                if section == "steps" or "warnings" in row:
                    warnings_list(row.get("warnings"), f"{where}/warnings")
        warnings_list(guide.get("warnings"), "warnings")

    print(f"\n가이드 데이터: {len(files)}개 파일, 문제 {len(problems)}개")
    for problem in problems:
        print(f"  {problem}")
    return len(problems)


def check_quest_spots(data, tasks, maps) -> int:
    """퀘스트 목표에 더한 위치: 목표가 dev 에 있는지, 맵·좌표·출처(from·link·checked)가 맞는지"""
    section = data.get("quest_spots", {})
    problems = []
    if not isinstance(section, dict):
        problems.append("quest_spots: 객체 필요")
        section = {}
    from app import quest_log, quests
    manual = quest_log.load_manual_quests()  # 게임 확인으로 고친 목표(objective_patch·add)까지 포함
    goals = {o.get("id"): quest_id for quest_id, task in tasks.items() if isinstance(task, dict)
             for o in quests.patched_task(task, manual.get(quest_id) or {}).get("objectives") or [] if isinstance(o, dict)}
    map_keys = {m.get("normalizedName") for m in maps.values() if isinstance(m, dict)}
    for objective, entry in section.items():
        if objective.startswith("_"):
            continue
        where = f"quest_spots/{objective}"
        if not isinstance(entry, dict) or not isinstance(entry.get("spots"), list) or not entry["spots"]:
            problems.append(f"{where}: spots 목록 필요 (비어 있으면 칸을 지움)")
            continue
        if objective not in goals:
            problems.append(f"{where}: tarkov.dev 에 없는 목표 id (퀘스트가 바뀌었으면 지우거나 옮김)")
        elif entry.get("quest") != goals[objective]:
            problems.append(f"{where}/quest: 그 목표의 퀘스트 id 와 다름")
        for index, spot in enumerate(entry["spots"]):
            at = f"{where}/spots/{index}"
            if not isinstance(spot, dict):
                problems.append(f"{at}: 객체 필요")
                continue
            if spot.get("map") not in map_keys:
                problems.append(f"{at}/map: 모르는 맵 구분값")
            if not all(type(spot.get(k)) in (int, float) and math.isfinite(spot[k]) for k in ("x", "y", "z")):
                problems.append(f"{at}: x·y·z 숫자 필요")
            if spot.get("from") not in ("wiki", "game"):
                problems.append(f"{at}/from: wiki 또는 game")
            if spot.get("from") == "wiki" and not str(spot.get("link", "")).startswith("https://"):
                problems.append(f"{at}/link: 위키 출처 주소 필요")
            try:
                datetime.strptime(spot.get("checked"), "%Y-%m-%d")
            except (TypeError, ValueError):
                problems.append(f"{at}/checked: 실제 날짜 YYYY-MM-DD 필요")
    print(f"\n퀘스트 목표 위치 보완 데이터: 문제 {len(problems)}개")
    for problem in problems:
        print(f"  {problem}")
    return len(problems)


def check_boss_zones(data) -> int:
    """보스 보정 좌표와 dev 우선 비교에 필요한 위치 이름을 확인한다."""
    zones = data.get("boss_zones", {})
    problems = []
    if not isinstance(zones, dict):
        problems.append("boss_zones: 객체 필요")
        zones = {}
    for map_key, entry in zones.items():
        if map_key.startswith("_"):
            continue
        if not isinstance(entry, dict):
            problems.append(f"boss_zones/{map_key}: 객체 필요")
            continue
        for field in (("source", "checked") if entry.get("source") == "game" else ("source", "link", "checked")):
            if not isinstance(entry.get(field), str) or not entry[field].strip():
                problems.append(f"boss_zones/{map_key}/{field}: 출처 정보 필요")
        try:
            checked = entry.get("checked", "")
            if not isinstance(checked, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", checked):
                raise ValueError
            datetime.strptime(checked, "%Y-%m-%d")
        except ValueError:
            problems.append(f"boss_zones/{map_key}/checked: 실제 날짜 YYYY-MM-DD 필요")
        rows = entry.get("zones")
        if not isinstance(rows, list):
            problems.append(f"boss_zones/{map_key}/zones: 비어 있지 않은 목록 필요")
            continue
        for index, zone in enumerate(rows):
            where = f"boss_zones/{map_key}/zones/{index}"
            if not isinstance(zone, dict):
                problems.append(f"{where}: 객체 필요")
                continue
            for field in ("x", "y", "z", "radius"):
                number = zone.get(field)
                if type(number) not in (int, float) or not math.isfinite(number) or (field == "radius" and number <= 0):
                    problems.append(f"{where}/{field}: 유한한 좌표·양수 반경 필요")
            if not isinstance(zone.get("spawn_location"), str) or not zone["spawn_location"].strip():
                problems.append(f"{where}/spawn_location: dev 등장 구역 이름 필요")
            bosses = zone.get("bosses")
            if not isinstance(bosses, list) or not bosses or any(not isinstance(mob, str) or not (mob.startswith("boss") or mob == "sectantPriest") for mob in bosses):
                problems.append(f"{where}/bosses: 보스 mob id 목록 필요")
    print(f"\n보스 구역 보완 데이터: 문제 {len(problems)}개")
    for problem in problems:
        print(f"  {problem}")
    return len(problems)


def check_floors(data) -> int:
    """건물별 층 보정: 범위 네모·낮은 층부터 올라가는 시작 높이·출처(game)·날짜."""
    problems = []
    floors = data.get("floors", {})
    if not isinstance(floors, dict):
        problems.append("floors: 객체 필요")
        floors = {}
    for map_key, entry in floors.items():
        if map_key.startswith("_"):
            continue
        where_map = f"floors/{map_key}"
        if not isinstance(entry, dict):
            problems.append(f"{where_map}: 객체 필요")
            continue
        if entry.get("source") != "game":
            problems.append(f"{where_map}/source: game 필요 (게임에서 잰 높이만)")
        if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", str(entry.get("checked", ""))):
            problems.append(f"{where_map}/checked: 날짜 YYYY-MM-DD 필요")
        hidden = entry.get("hidden_layers", [])
        if not isinstance(hidden, list) or any(not isinstance(name, str) or not name for name in hidden):
            problems.append(f"{where_map}/hidden_layers: 층 이름 목록 필요")
        buildings = entry.get("buildings")
        if not isinstance(buildings, list) or not buildings:
            problems.append(f"{where_map}/buildings: 비어 있지 않은 목록 필요")
            continue
        for index, building in enumerate(buildings):
            where = f"{where_map}/buildings/{index}"
            if not isinstance(building, dict):
                problems.append(f"{where}: 객체 필요")
                continue
            bounds = building.get("bounds")
            if not (isinstance(bounds, list) and 2 <= len(bounds) <= 200 and all(
                    isinstance(p, list) and len(p) == 2 and all(type(n) in (int, float) and math.isfinite(n) for n in p)
                    for p in bounds)):
                problems.append(f"{where}/bounds: 꼭짓점 [[x, z], …] 3개 이상 (또는 네모의 두 모서리) 필요")
            if not isinstance(building.get("name"), str) or not building["name"].strip():
                problems.append(f"{where}/name: 건물 설명 필요")
            levels = building.get("levels")
            if not isinstance(levels, list) or not levels:
                problems.append(f"{where}/levels: 층 목록 필요")
                continue
            last = None
            for at, level in enumerate(levels):
                if not isinstance(level, dict) or not isinstance(level.get("floor"), str) or not level["floor"]:
                    problems.append(f"{where}/levels/{at}/floor: 층 이름 필요")
                    continue
                start = level.get("from")
                if at == 0:
                    if start is not None:
                        problems.append(f"{where}/levels/0/from: 가장 낮은 층은 from 없이")
                elif type(start) not in (int, float) or not math.isfinite(start) or (last is not None and start <= last):
                    problems.append(f"{where}/levels/{at}/from: 아래층보다 높은 시작 높이 필요")
                else:
                    last = start
    print(f"\n건물 층 보정 데이터: 문제 {len(problems)}개")
    for problem in problems:
        print(f"  {problem}")
    return len(problems)


def check_enemy_spawns(data, maps) -> int:
    """BD 보정의 숫자·출처·날짜와 dev 위치 이름·병합 참조를 검사한다."""
    entries = data.get("enemy_spawns", {})
    problems = []
    if not isinstance(entries, dict):
        problems.append("enemy_spawns: 객체 필요")
        entries = {}
    for map_key, groups in entries.items():
        if map_key.startswith("_"):
            continue
        where_map = f"enemy_spawns/{map_key}"
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", map_key) or not isinstance(groups, dict):
            problems.append(f"{where_map}: 맵 이름과 비어 있지 않은 객체 필요")
            continue
        dev_maps = [m for m in maps.values() if isinstance(m, dict) and m.get("normalizedName") == map_key]
        dev_names = set()
        boss_names = set()  # 보스·군즈·컬티스트 등장 구역 이름 (옮기기·숨기기 보정용)
        for m in dev_maps:
            bosses = m.get("bosses")
            for boss in bosses if isinstance(bosses, list) else []:
                if isinstance(boss, dict) and boss.get("mob") != "blackDivision":
                    for location in boss.get("spawnLocations") if isinstance(boss.get("spawnLocations"), list) else []:
                        if isinstance(location, dict) and isinstance(location.get("name"), str):
                            boss_names.add(location["name"])
                if not isinstance(boss, dict) or boss.get("mob") != "blackDivision":
                    continue
                locations = boss.get("spawnLocations")
                for location in locations if isinstance(locations, list) else []:
                    if isinstance(location, dict) and isinstance(location.get("name"), str):
                        dev_names.add(location["name"])
        if not dev_maps:
            problems.append(f"{where_map}: dev에 없는 맵")
        for name, entry in groups.items():
            where = f"{where_map}/{name}"
            manual = isinstance(entry, dict) and entry.get("manual") is True and re.fullmatch(r"manual_[1-9][0-9]*", name)
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", name) or (name not in dev_names and not manual):
                problems.append(f"{where}: dev Black Division 등장 구역 이름 필요")
            if isinstance(entry, dict) and "zone" in entry:
                # dev 보스 구역을 옮기거나 숨긴 보정 (편집 도구가 만듦): 이름 = <구역>_<종류>, 게임 확인만
                problems.pop()  if problems and problems[-1].startswith(f"{where}: dev Black Division") else None
                if (entry.get("kind") not in ("boss", "goons", "cultist") or not isinstance(entry["zone"], str)
                        or name != f"{entry['zone']}_{entry.get('kind')}" or entry["zone"] not in boss_names):
                    problems.append(f"{where}: dev 보스 등장 구역 이름과 종류 필요")
                for field in ("x", "y", "z"):
                    number = entry.get(field)
                    if type(number) not in (int, float) or not math.isfinite(number):
                        problems.append(f"{where}/{field}: 유한한 숫자 좌표 필요")
                if entry.get("from") != "game":
                    problems.append(f"{where}/from: game 필요 (dev 값을 고치는 건 게임 확인만)")
                if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", str(entry.get("checked", ""))):
                    problems.append(f"{where}/checked: 날짜 YYYY-MM-DD 필요")
                if "hidden" in entry and type(entry["hidden"]) is not bool:
                    problems.append(f"{where}/hidden: 참/거짓 필요")
                continue
            if not isinstance(entry, dict):
                problems.append(f"{where}: 객체 필요")
                continue
            for field in ("x", "y", "z"):
                number = entry.get(field)
                if type(number) not in (int, float) or not math.isfinite(number):
                    problems.append(f"{where}/{field}: 유한한 숫자 좌표 필요")
            counts = entry.get("count")
            if not isinstance(counts, list) or len(counts) != 2 or not all(type(n) is int and n >= 1 for n in counts) or counts[0] > counts[1]:
                problems.append(f"{where}/count: 양의 정수 [최소, 최대] 필요")
            if entry.get("from") not in ("game", "wiki"):
                problems.append(f"{where}/from: game 또는 wiki 필요")
            if not isinstance(entry.get("note"), str) or (not entry["note"].strip() and not manual):
                problems.append(f"{where}/note: 보정 근거 필요")
            try:
                checked = entry.get("checked", "")
                if not isinstance(checked, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", checked):
                    raise ValueError
                datetime.strptime(checked, "%Y-%m-%d")
            except ValueError:
                problems.append(f"{where}/checked: 실제 날짜 YYYY-MM-DD 필요")
            for flag in ("inside", "estimated", "manual"):
                if flag in entry and type(entry[flag]) is not bool:
                    problems.append(f"{where}/{flag}: 참/거짓 필요")
            if "merge_into" in entry:
                target = entry["merge_into"]
                if not isinstance(target, str) or target == name or target not in dev_names or not isinstance(groups.get(target), dict):
                    problems.append(f"{where}/merge_into: 같은 맵의 다른 보정·dev 구역 이름 필요")
                elif groups[target].get("merge_into"):
                    problems.append(f"{where}/merge_into: 다단계·순환 병합 금지")
    print(f"\n적 등장 보완 데이터: 문제 {len(problems)}개")
    for problem in problems:
        print(f"  {problem}")
    return len(problems)


def sources(quest_id, tasks, manual, maps, names):
    """화면과 같은 필드별 우선순위로 이름·맵의 출처를 센다."""
    from app import tarkov_api

    entry = manual.get(quest_id, {})
    task = tasks.get(quest_id) or tasks.get(entry.get("same_as"))
    game = entry.get("from") == "game"
    name = entry.get("name") or entry.get("name_en")
    if game and name:
        name_source = "corrections"
    elif task:
        key = task.get("name")
        name_source = "dev" if any(bundle.get(key) for bundle in names) else "unknown"
    elif tarkov_api.spt_quest_name(quest_id, "ko"):
        name_source = "SPT only"
    else:
        name_source = "corrections" if name else "unknown"
    location = tarkov_api.spt_quest_location(quest_id)
    if game and entry.get("maps") is not None:
        map_source = "corrections"
    elif task:
        map_source = "dev"  # 맵 제한이 없는 dev 퀘스트도 포함
    elif location in maps or location == "any":
        map_source = "SPT only"
    else:
        map_source = "corrections" if entry.get("maps") else "unknown"
    return name_source, map_source


def main() -> int:
    guide_problems = check_guides()
    # 가이드 검사는 게임 설치·캐시·외부 패키지 없이도 별도로 실행할 수 있다.
    if "--guides-only" in sys.argv[1:]:
        return int(bool(guide_problems))

    from app import corrections, quest_log, quests, settings, tarkov_api

    try:
        data = corrections.load()
        boss_problems = check_boss_zones(data) + check_floors(data)
        if not all(isinstance(data.get(key), dict) for key in ("quests", "btr_stops")):
            raise ValueError("corrections.json이 없거나 읽을 수 없습니다")
        for section in ("quests", "btr_stops"):
            if any(not isinstance(value, dict) for key, value in data[section].items() if not key.startswith("_")):
                raise ValueError("corrections.json 항목 형식을 읽을 수 없습니다")
        tasks = tarkov_api.load("tasks")["tasks"]
        maps = tarkov_api.load("maps")["maps"]
        spot_problems = check_quest_spots(data, tasks, maps)
        names = [tarkov_api.load("tasks_ko"), tarkov_api.load("tasks_en")]
        spt = tarkov_api._read_spt_names()
        if not spt:
            raise ValueError("SPT 이름 캐시가 없거나 읽을 수 없습니다")
        if not all(isinstance(value, dict) for value in [tasks, maps, *names]):
            raise ValueError("dev 캐시 형식을 읽을 수 없습니다")
        enemy_problems = check_enemy_spawns(data, maps)
        if not settings.logs_dir().is_dir():
            raise ValueError("게임 로그 폴더를 읽을 수 없습니다")
        # 이 PC에 남은 전체 로그에서 중복 id는 한 번만 센다.
        events = quest_log.read_quest_events(since=datetime.min)
        manual = quest_log.load_manual_quests()
        btr = corrections.section("btr_stops")
        ids = sorted({event["quest_id"] for event in events})
        counts = [Counter(), Counter()]
        for quest_id in ids:
            for counter, source in zip(counts, sources(quest_id, tasks, manual, maps, names)):
                counter[source] += 1

        print(f"합계: 로그 퀘스트 {len(ids)}개 (이 PC에 남은 전체 로그, 반복 과제 제외)")
        for field, counter in zip(("이름", "맵"), counts):
            print(f"  {field}: " + " / ".join(f"{source} {counter[source]}" for source in ("dev", "corrections", "SPT only", "unknown")))
        print("  SPT only = 그 필드가 SPT에서 옴 / unknown = 이름 또는 맵 정보 없음")

        print("\n은퇴 가능 (dev 가 따라잡음)")
        kept = []
        retired = []
        supplements = []
        supplement_problems = []
        for quest_id, entry in manual.items():
            task = tasks.get(quest_id) or tasks.get(entry.get("same_as"))
            # prep_add: dev 준비물에 덧붙이기만 하는 칸 (dev 가 모르는 필수 준비물). dev 가 따라잡는 칸이 아니라 은퇴 대상이 아니다.
            adds = entry.get("prep_add")
            if adds is not None:
                dev_name = next((bundle[task["name"]] for bundle in reversed(names)
                                 if task and isinstance(bundle.get(task.get("name")), str)), None)
                items = []
                for index, extra in enumerate(adds if isinstance(adds, list) else [None]):
                    # quests.py 는 형식이 틀린 항목을 말없이 건너뛰므로 여기서 잡는다.
                    if (not isinstance(extra, dict) or extra.get("kind") not in ("가져가기", "무기", "착용")
                            or not isinstance(extra.get("text"), str) or not extra["text"].strip()):
                        supplement_problems.append(f"  quests/{quest_id}/prep_add/{index}: kind(가져가기·무기·착용)와 text 필요")
                    else:
                        items.append(extra["text"])
                supplements.append(f"  {quest_id}: {dev_name or quest_id} — {', '.join(items) or '없음'}")
                if set(entry) <= {"prep_add", "link", "from", "checked", "same_as"}:
                    continue  # 덧붙이기만 있는 항목: 뺄 칸이 없음
            fields = []
            if task:
                if (entry.get("name") or entry.get("name_en")) and any(bundle.get(task.get("name")) for bundle in names):
                    fields.append("name")
                if entry.get("maps") is not None and (task.get("map") or any(obj.get("maps") for obj in task.get("objectives", []))):
                    fields.append("maps")
                if quests._has_manual_objectives(entry) and task.get("objectives"):
                    fields.append("objectives")
                # objective_patch·add: dev 목표에서 틀린 칸만 덮어씀. 목표 id 가 dev 에서 사라지면 보정이 말없이 안 먹으므로 여기서 잡는다
                patch, add = entry.get("objective_patch") or {}, entry.get("objective_add") or []
                if patch or add:
                    dev_objectives = {o.get("id"): o for o in task.get("objectives", [])}
                    map_keys = {m.get("normalizedName") for m in maps.values() if isinstance(m, dict)}
                    map_ids = {m.get("normalizedName"): m.get("id") for m in maps.values() if isinstance(m, dict)}
                    caught_up = []
                    for objective_id, change in patch.items():
                        dev_objective = dev_objectives.get(objective_id)
                        if dev_objective is None:
                            supplement_problems.append(f"  quests/{quest_id}/objective_patch/{objective_id}: dev 에 이 목표 id 가 없음 (dev 가 바뀜 → 다시 확인)")
                            continue
                        bad = [k for k in change.get("maps") or [] if k not in map_keys]
                        if bad:
                            supplement_problems.append(f"  quests/{quest_id}/objective_patch/{objective_id}: 모르는 맵 {bad}")
                        same = (not change.get("remove") and not (change.get("text") or change.get("original"))
                                and all(dev_objective.get(f) == change[f] for f in quests.PATCH_FIELDS if f in change)
                                and ("maps" not in change or set(dev_objective.get("maps") or []) == {map_ids.get(k) for k in change["maps"]}))
                        if same:
                            caught_up.append(objective_id)
                    for index, extra in enumerate(add):
                        if not isinstance(extra, dict) or not extra.get("id") or not (extra.get("text") or extra.get("original")):
                            supplement_problems.append(f"  quests/{quest_id}/objective_add/{index}: id 와 text·original 필요")
                    fields.append(f"objective_patch {len(patch)}개" + (f" (dev 가 따라잡은 것 {caught_up})" if caught_up else "")
                                  + (f", objective_add {len(add)}개" if add else ""))
            label = entry.get("name_en") or entry.get("name") or next(
                (bundle[task["name"]] for bundle in reversed(names) if task and isinstance(bundle.get(task.get("name")), str)), quest_id)
            row = f"  {quest_id}: {label} — dev 공통 필드: {', '.join(fields) or '없음'}"
            if adds is not None:
                row += " (prep_add 는 남김)"
            if set(entry) <= {"same_as", "checked"}:
                continue  # 이미 연결만 남긴 항목 (은퇴 끝남)
            if entry.get("from") == "game":
                kept.append(row)
            elif task and quest_id not in tasks:
                # 로그 번호와 dev 번호가 달라 same_as 로 잇는 항목: 연결은 꼭 필요, 나머지 칸은 dev 가 씀
                retired.append(row + " → same_as 연결만 남기고 나머지 칸은 빼도 됨")
            elif task:
                retired.append(row)
        for key in btr:
            if any(m.get("normalizedName") == key and m.get("btrStops") for m in maps.values()):
                retired.append(f"  BTR {key}: dev에 btrStops 있음")
        print("\n".join(retired) or "  없음")
        print("직접 확인값 — 유지")
        print("\n".join(kept) or "  없음")
        print("준비물 덧붙이기 (prep_add) — 유지")
        print("\n".join(supplements) or "  없음")
        if supplement_problems:
            print(f"준비물 덧붙이기 문제 {len(supplement_problems)}개")
            print("\n".join(supplement_problems))

        print("\n확인 필요")
        pending = [qid for qid in ids if qid not in tasks and qid not in manual
                   and not quests.hidden_quest(qid, tasks, manual)]
        for quest_id in pending:
            name = tarkov_api.spt_quest_name(quest_id, "ko") or "없음"
            location = tarkov_api.spt_quest_location(quest_id)
            map_name = maps.get(location, {}).get("normalizedName") or location or "없음"
            print(f"  {quest_id}: SPT 이름={name} / 맵={map_name}")
        if not pending:
            print("  없음")

        print("\n출처 주의")
        warnings = []
        for section, entries in (("quests", manual), ("btr_stops", btr)):
            for key, entry in entries.items():
                flags = []
                if entry.get("from", entry.get("source")) == "blog":
                    flags.append("blog 출처")
                if not entry.get("link") and not set(entry) <= {"same_as", "checked"}:
                    flags.append("link 없음")
                if not entry.get("checked"):
                    flags.append("checked 없음")
                if entry.get("check"):
                    flags.append("check 검토 대기")
                if flags:
                    warnings.append(f"  {section}/{key}: {', '.join(flags)}")
        print("\n".join(warnings) or "  없음")
        return int(bool(guide_problems or boss_problems or enemy_problems or supplement_problems or spot_problems))  # 기존 검토 항목은 오류가 아니다.
    except (OSError, ValueError, KeyError, TypeError, AttributeError, tarkov_api.DataError) as error:
        print(f"파일/캐시를 읽지 못했습니다: {error}")
        return 1


if __name__ == "__main__":
    sys.exit(main())

"""기존 저장본과 게임 로그로 보완 데이터의 검토 목록을 출력한다. 다운로드·저장 없음."""

import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import corrections, quest_log, quests, settings, tarkov_api


def sources(quest_id, tasks, manual, maps, names):
    """화면과 같은 필드별 우선순위로 이름·맵의 출처를 센다."""
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
    try:
        data = corrections.load()
        if not all(isinstance(data.get(key), dict) for key in ("quests", "btr_stops")):
            raise ValueError("corrections.json이 없거나 읽을 수 없습니다")
        for section in ("quests", "btr_stops"):
            if any(not isinstance(value, dict) for key, value in data[section].items() if not key.startswith("_")):
                raise ValueError("corrections.json 항목 형식을 읽을 수 없습니다")
        tasks = tarkov_api.load("tasks")["tasks"]
        maps = tarkov_api.load("maps")["maps"]
        names = [tarkov_api.load("tasks_ko"), tarkov_api.load("tasks_en")]
        spt = tarkov_api._read_spt_names()
        if not spt:
            raise ValueError("SPT 이름 캐시가 없거나 읽을 수 없습니다")
        if not all(isinstance(value, dict) for value in [tasks, maps, *names]):
            raise ValueError("dev 캐시 형식을 읽을 수 없습니다")
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
        for quest_id, entry in manual.items():
            task = tasks.get(quest_id) or tasks.get(entry.get("same_as"))
            fields = []
            if task:
                if (entry.get("name") or entry.get("name_en")) and any(bundle.get(task.get("name")) for bundle in names):
                    fields.append("name")
                if entry.get("maps") is not None and (task.get("map") or any(obj.get("maps") for obj in task.get("objectives", []))):
                    fields.append("maps")
                if quests._has_manual_objectives(entry) and task.get("objectives"):
                    fields.append("objectives")
            row = f"  {quest_id}: {entry.get('name_en') or entry.get('name') or quest_id} — dev 공통 필드: {', '.join(fields) or '없음'}"
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
                if not entry.get("link"):
                    flags.append("link 없음")
                if not entry.get("checked"):
                    flags.append("checked 없음")
                if entry.get("check"):
                    flags.append("check 검토 대기")
                if flags:
                    warnings.append(f"  {section}/{key}: {', '.join(flags)}")
        print("\n".join(warnings) or "  없음")
        return 0  # 검토할 항목은 오류가 아니다.
    except (OSError, ValueError, KeyError, TypeError, AttributeError, tarkov_api.DataError) as error:
        print(f"파일/캐시를 읽지 못했습니다: {error}")
        return 1


if __name__ == "__main__":
    sys.exit(main())

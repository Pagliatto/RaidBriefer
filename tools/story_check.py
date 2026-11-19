"""스토리 데이터 점검 도구 (개발용).

data/story/*.json 을 검사해서 문제를 찾는다.
1) 위키 목표 목록과 단계(en)가 순서·개수까지 같은지 (The Ticket은 개수·내용만)
2) 맵 구분값이 tarkov.dev에 있는 맵인지
3) 단계 id가 겹치지 않는지
4) (Optional) 단계에 선택 표시가 있는지 / 없는 단계에 잘못 붙지 않았는지
5) 한글 문장에 맵 이름이 있는데 맵 표시가 빠진 단계
6) 갈림길: when 이 존재하는 갈림길·선택지를 가리키는지, 엔딩마다 길이 끝까지 이어지는지
7) 준비물 종류, 사진 주소

사용: .venv\\Scripts\\python.exe -X utf8 tools\\story_check.py
"""

import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from app import tarkov_api  # noqa: E402
from story_wiki_dump import objectives, section_text  # noqa: E402

WIKI_PAGE = {
    "tour": "Tour", "falling-skies": "Falling Skies", "blue-fire": "Blue Fire",
    "accidental-witness": "Accidental Witness", "the-unheard": "The Unheard",
    "they-are-already-here": "They Are Already Here", "the-labyrinth": "The Labyrinth (story chapter)",
    "batya": "Batya", "boreas": "Boreas", "the-ticket": "The Ticket",
}
PREP_KINDS = {"열쇠", "가져가기", "무기", "착용", "제출"}
# 한글 문장 속 맵 이름 → 맵 구분값 (맵 표시 빠짐 찾기용)
KO_MAPS = {
    "세관": "customs", "삼림": "woods", "해안선": "shoreline", "인터체인지": "interchange",
    "공장": "factory", "리저브": "reserve", "등대에서": "lighthouse", "타르코프 시내": "streets-of-tarkov",
    "그라운드 제로": "ground-zero", "연구소": "the-lab", "쇄빙선": "icebreaker", "미궁": "the-labyrinth",
}

problems = []


def report(chapter, msg):
    problems.append(f"[{chapter}] {msg}")


def check_when(ch, when, where, all_chapters, quest_ids):
    """조건 하나하나가 실제로 있는 것을 가리키는지
    - "kerman"                  : 이 챕터의 갈림길
    - "falling-skies@case"      : 다른 챕터의 갈림길
    - "done:falling-skies"      : 챕터 완료 (true/false)
    - "quest:id1,id2"           : 퀘스트 완료 (true/false)
    """
    local = {s["choice"]["id"]: [o["id"] for o in s["choice"]["options"]] for s in ch["sections"] if "choice" in s}
    for key, want in (when or {}).items():
        wants = want if isinstance(want, list) else [want]
        if key.startswith("done:"):
            if key[5:] not in all_chapters:
                report(ch["id"], f"없는 챕터 완료 조건 '{key}' ({where})")
            if not all(isinstance(w, bool) for w in wants):
                report(ch["id"], f"'{key}' 는 true/false 여야 함 ({where})")
        elif key.startswith("quest:"):
            for qid in key[6:].split(","):
                if qid not in quest_ids:
                    report(ch["id"], f"tarkov.dev에 없는 퀘스트 id '{qid}' ({where})")
        elif "@" in key:
            other, cid = key.split("@", 1)
            opts = all_chapters.get(other, {}).get(cid)
            if opts is None:
                report(ch["id"], f"없는 다른 챕터 갈림길 '{key}' ({where})")
            elif any(w not in opts for w in wants):
                report(ch["id"], f"'{key}' 에 없는 선택지 {wants} ({where})")
        elif key not in local:
            report(ch["id"], f"when 이 없는 갈림길 '{key}' 를 가리킴 ({where})")
        elif any(w not in local[key] for w in wants):
            report(ch["id"], f"when 의 선택지 '{key}={wants}' 가 없음 ({where})")


def check_choices(ch):
    """가능한 엔딩(이 챕터 갈림길) 조합마다 단계 수 계산. 다른 조건은 세지 않음."""
    choices = {s["choice"]["id"]: [o["id"] for o in s["choice"]["options"]] for s in ch["sections"] if "choice" in s}
    if not choices:
        return None

    # 모든 선택 조합을 따라가 보기 (화면의 visibleItems 와 같은 규칙)
    results = {}

    def walk(picks):
        active, steps, pending = {}, 0, None
        def local_ok(when):
            local_when = {k: v for k, v in (when or {}).items() if k in choices}
            return all((active.get(k) in v) if isinstance(v, list) else active.get(k) == v
                       for k, v in local_when.items())

        for s in ch["sections"]:
            if not local_ok(s.get("when")):
                continue
            # when_any: 다른 조건(퀘스트 완료 등)이 섞인 묶음은 "아직 안 됨"으로 보고 셈
            if s.get("when_any") and not any(
                    local_ok(w) and all(k in choices for k in w) for w in s["when_any"]):
                continue
            if s.get("notice"):
                continue
            if "choice" in s:
                cid = s["choice"]["id"]
                if cid not in picks:
                    pending = cid
                    break
                active[cid] = picks[cid]
                continue
            steps += len(s["steps"])
        if pending:
            for opt in choices[pending]:
                walk({**picks, pending: opt})
        else:
            results[" / ".join(f"{k}={v}" for k, v in picks.items())] = steps

    walk({})
    return results


def main():
    tarkov_api.update_all()
    map_keys = {m["normalizedName"] for m in tarkov_api.load("maps")["maps"].values()}
    quest_ids = set(tarkov_api.load("tasks")["tasks"].keys())
    all_ids = Counter()
    summary = []

    paths = sorted((ROOT / "data" / "story").glob("*.json"))
    loaded = [json.loads(p.read_text(encoding="utf-8")) for p in paths]
    # 챕터 id → {갈림길 id: [선택지 id]} (다른 챕터 조건 점검용)
    all_chapters = {c["id"]: {s["choice"]["id"]: [o["id"] for o in s["choice"]["options"]]
                              for s in c["sections"] if "choice" in s} for c in loaded}

    for ch in loaded:
        cid = ch["id"]
        # 6) 조건이 실제로 있는 것을 가리키는지 (구간·단계·준비물)
        for s in ch["sections"]:
            where = s.get("title") or f"갈림길 {s['choice']['id']}"
            for item, name in [(s, where)] + [(st, st["ko"]) for st in s.get("steps", [])] + \
                              [(p, p["text"]) for p in s.get("prep", [])]:
                check_when(ch, item.get("when"), name, all_chapters, quest_ids)
                for w in item.get("when_any") or []:
                    check_when(ch, w, name + " (when_any)", all_chapters, quest_ids)
            for o in s.get("choice", {}).get("options", []):
                check_when(ch, o.get("blocked_when"), f"선택지 {o['id']} 막힘 조건", all_chapters, quest_ids)
        steps = [st for s in ch["sections"] for st in s.get("steps", [])]
        all_ids.update(st["id"] for st in steps)

        # 1) 위키와 비교
        def norm(t):  # 위키 원문의 겹친 띄어쓰기 같은 차이는 무시
            return re.sub(r"\s+", " ", t).strip()
        wiki = [norm(o["en"]) for o in objectives(section_text(WIKI_PAGE[cid], "Objectives"))]
        ours = [norm(st["en"]) for st in steps]
        if cid == "the-ticket":
            # 엔딩별로 같은 문장이 반복되므로 개수·내용만 비교
            if Counter(ours) != Counter(wiki):
                missing = Counter(wiki) - Counter(ours)
                extra = Counter(ours) - Counter(wiki)
                if missing:
                    report(cid, f"위키에 있는데 빠진 목표: {dict(missing)}")
                if extra:
                    report(cid, f"위키에 없는 목표: {dict(extra)}")
        else:
            if ours != wiki:
                for i, (a, b) in enumerate(zip(ours, wiki)):
                    if a != b:
                        report(cid, f"{i}번째 단계가 위키와 다름: 우리='{a}' / 위키='{b}'")
                        break
                if len(ours) != len(wiki):
                    report(cid, f"단계 수가 다름: 우리 {len(ours)} / 위키 {len(wiki)}")

        for s in ch["sections"]:
            if "choice" in s or s.get("notice"):
                continue
            if not s.get("steps"):
                report(cid, f"단계가 없는 구간: {s['title']}")
            # 2) 맵
            for value in [s.get("map")] + [st.get("map") for st in s["steps"]]:
                for key in (value if isinstance(value, list) else [value]):  # 맵이 여러 개일 수도 있음
                    if key and key not in map_keys:
                        report(cid, f"없는 맵 구분값 '{key}' ({s['title']})")
            # 7) 준비물, 사진
            for p in s.get("prep", []):
                if p["kind"] not in PREP_KINDS:
                    report(cid, f"이상한 준비물 종류 '{p['kind']}' ({s['title']})")
            for im in s.get("images", []):
                if not (im.get("thumb") or "").startswith("https://static.wikia.nocookie.net/"):
                    report(cid, f"사진 주소 이상: {im.get('caption')}")
            for st in s["steps"]:
                # 4) 선택 표시
                is_opt_en = st["en"].startswith("(Optional)")
                if is_opt_en and not st["optional"]:
                    report(cid, f"(Optional)인데 선택 표시 없음: {st['ko']}")
                if st["optional"] and not is_opt_en:
                    report(cid, f"선택이 아닌데 선택 표시: {st['ko']}")
                if "[조건]" in st["ko"]:
                    report(cid, f"[조건] 표시가 남음 (when 또는 manual 로 바꿔야 함): {st['ko']}")
                # 5) 맵 이름은 있는데 맵 표시 없음
                # (상인 대화·접근 권한·챙겨 가기·핸드북처럼 그 맵에 갈 필요 없는 문장은 제외)
                skip = ("접근 권한", "에게 ", "챙겨", "핸드북", "출입 카드", "수송편")
                if not st.get("map") and not any(w in st["ko"] for w in skip):
                    for word, key in KO_MAPS.items():
                        if word in st["ko"]:
                            report(cid, f"맵 표시 빠짐? '{st['ko']}' → {key}")
                            break

        routes = check_choices(ch)
        summary.append((ch["name"], len(steps), len(ch["sections"]), routes))

    # 3) id 중복
    for sid, n in all_ids.items():
        if n > 1:
            report("전체", f"단계 id 중복: {sid} ({n}번)")

    print("== 챕터 요약 ==")
    for name, n, secs, routes in summary:
        print(f"  {name:24s} 단계 {n:3d}  구간 {secs:2d}")
        for route, cnt in (routes or {}).items():
            print(f"      길 [{route}] → {cnt}단계")
    print(f"\n== 문제 {len(problems)}개 ==")
    for p in problems:
        print("  " + p)


if __name__ == "__main__":
    main()

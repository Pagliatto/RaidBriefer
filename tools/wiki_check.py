"""tarkov.dev 퀘스트를 공식 위키와 비교하는 개발용 점검 도구 (프로그램 실행과는 상관없음).

dev 가 패치를 늦게 따라가서 틀린 퀘스트(예: Job for a Patriot 맵)를 찾으려고 만듦.
자동으로 고치지 않고 '다른 것' 목록만 보여 준다 → 사람이 보고 corrections.json 에 적는다.

비교하는 것
  1) 맵: 위키 정보상자 location ↔ dev 퀘스트·목표의 맵 (corrections.json 의 game 맵 보정이 있으면 그것)
  2) 개수: dev 목표의 count(2 이상)가 위키 Objectives 문단에 숫자로 나오는지

위키 원문은 cache/wiki_quests.json 에 저장해 두고 다시 쓴다 (위키에 부담 안 가게, 50개씩 묶어서 받음).

사용: .venv\\Scripts\\python.exe -X utf8 tools\\wiki_check.py [--refresh] [--json 저장할파일]
"""

import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import paths, quest_log, quests, tarkov_api  # noqa: E402

API = "https://escapefromtarkov.fandom.com/api.php"
HEADERS = {"User-Agent": "RaidBriefer/0.1 (unofficial fan tool)"}
CACHE_FILE = paths.CACHE_DIR / "wiki_quests.json"
BATCH = 50  # 위키 API 한 번에 최대 50쪽

# 같은 장소로 보는 dev 맵 (야간 공장 = 공장, 21+ = 그라운드 제로 등)
SAME_MAP = {
    "night-factory": "factory", "infected-factory": "factory",
    "ground-zero-21": "ground-zero", "ground-zero-tutorial": "ground-zero",
    "the-lab-dark": "the-lab", "infected-lab": "the-lab",
}
# 위키에서 쓰는 다른 이름
WIKI_ALIAS = {"labs": "the-lab", "lab": "the-lab", "streets": "streets-of-tarkov", "labyrinth": "the-labyrinth"}
# 사람이 보고 '오류 아님'으로 확인한 퀘스트 (목록에서 뺌). 이유를 같이 적음 (2026-11-28 첫 점검)
KNOWN_OK = {
    "67a0970744893b9f3f0d9b68": "Offensive Reconnaissance: 해안선 벙커 문 너머가 래비린스라 dev 맵이 맞음",
    "63a9b36cc31b00242d28a99f": "Slaughterhouse: 위키 정보상자가 목표에 없는 맵까지 적음, 목표는 dev 와 같음",
    "639136df4b15ca31f76bc31f": "Huntsman Path - Administrator: BEAR(리저브) / USEC(등대) 퀘스트가 따로 있음",
    "6a45208043b8d7604d00b8d5": "Huntsman Path - Administrator: BEAR(리저브) / USEC(등대) 퀘스트가 따로 있음",
    "60c0c018f7afb4354815096a": "Huntsman Path - Factory Chief: 목표는 위키와 같음 (정보상자만 인터체인지 추가)",
    "608974af4b05530f55550c21": "Reserve Expert: 위키 목표에 개수가 안 적혀 있을 뿐",
}
# 레이드 맵이 아닌 곳 (아레나 PVE ZONE 퀘스트)
IGNORE_WIKI = {"arena"}
# 위키 location 이 '맵 상관없음' 인 경우
ANY_WORDS = {"any", "any location", "all", "all locations", "any map", "various", "multiple"}


def _title_from_link(link: str) -> str:
    return unquote(link.rsplit("/wiki/", 1)[-1]).replace("_", " ")


def fetch_wikitexts(titles: list[str], cache: dict) -> dict:
    """제목 → 위키 원문. 캐시에 없는 것만 50개씩 받음."""
    todo = [t for t in titles if t not in cache]
    for i in range(0, len(todo), BATCH):
        chunk = todo[i:i + BATCH]
        try:
            response = requests.get(API, headers=HEADERS, timeout=60, params={
                "action": "query", "prop": "revisions", "rvprop": "content", "rvslots": "main",
                "titles": "|".join(chunk), "redirects": 1, "format": "json", "formatversion": 2,
            })
            response.raise_for_status()
            data = response.json()
        except (requests.RequestException, ValueError) as error:
            print(f"  위키 받기 실패 ({error}) → 이 묶음은 저장 안 함, 다음 실행 때 다시", file=sys.stderr)
            continue
        query = data.get("query")
        if data.get("error") or not isinstance(query, dict):
            print(f"  위키 오류 응답 {data.get('error')} → 이 묶음은 저장 안 함, 다음 실행 때 다시", file=sys.stderr)
            continue
        # 요청한 제목 → 실제 쪽 제목 (대소문자 정리·넘겨주기)
        rename = {}
        for kind in ("normalized", "redirects"):
            for r in query.get(kind, []):
                rename[r["from"]] = r["to"]
        pages = {p["title"]: p for p in query.get("pages", [])}
        for title in chunk:
            final = title
            while final in rename:
                final = rename[final]
            page = pages.get(final)
            revs = (page or {}).get("revisions") or []
            if revs:
                cache[title] = revs[0]["slots"]["main"]["content"]
            elif page and page.get("missing"):
                cache[title] = None  # 위키에 정말 없는 쪽만 '없음'으로 저장 (응답에 빠진 것은 다음에 다시)
        print(f"  위키 받음 {min(i + BATCH, len(todo))}/{len(todo)}", file=sys.stderr)
        time.sleep(1)
    return cache


def _plain(text: str) -> str:
    text = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]+)\]\]", r"\1", text)
    return re.sub(r"'''?|<[^>]+>|\{\{[^}]*\}\}", " ", text)


def _key(name: str) -> str:
    key = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    key = WIKI_ALIAS.get(key, key)
    return SAME_MAP.get(key, key)


def wiki_locations(wikitext: str):
    """정보상자 location → 맵 키 집합. 'any' 면 문자열 "any", 없으면 None"""
    m = re.search(r"^\|\s*location\s*=(.*)$", wikitext, re.M | re.I)
    if not m:
        return None
    raw = m[1].strip()
    links = re.findall(r"\[\[([^|\]]+)(?:\|[^\]]*)?\]\]", raw)
    parts = links or re.split(r",|<br\s*/?>|\bor\b|\band\b|/", _plain(raw))
    names = [p.strip().strip("'").strip() for p in parts if p.strip().strip("'").strip()]
    if not names:
        return None
    if any(n.lower() in ANY_WORDS for n in names):
        return "any"
    return {_key(n) for n in names}


def wiki_objectives(wikitext: str) -> str:
    m = re.search(r"==\s*Objectives\s*==(.*?)(?=\n==[^=]|\Z)", wikitext, re.S | re.I)
    return _plain(m[1]) if m else ""


def main() -> int:
    refresh = "--refresh" in sys.argv
    json_out = sys.argv[sys.argv.index("--json") + 1] if "--json" in sys.argv else None

    tasks, tr = tarkov_api.get_tasks()
    maps = tarkov_api.load("maps")["maps"]
    map_key = {mid: SAME_MAP.get(m["normalizedName"], m["normalizedName"]) for mid, m in maps.items()}
    known_keys = set(map_key.values())
    manual = quest_log.load_manual_quests()

    cache = {}
    if CACHE_FILE.exists() and not refresh:
        cache = json.loads(CACHE_FILE.read_text(encoding="utf-8")).get("pages", {})
    titles = {qid: _title_from_link(t["wikiLink"]) for qid, t in tasks.items() if t.get("wikiLink")}
    fetch_wikitexts(sorted(set(titles.values())), cache)
    CACHE_FILE.write_text(json.dumps({"fetched": datetime.now().isoformat(timespec="seconds"), "pages": cache},
                                     ensure_ascii=False), encoding="utf-8")

    # dev 목표 영어 문장 속 맵 이름 → 맵 키 (긴 이름부터: 'The Labyrinth' 가 'The Lab' 보다 먼저)
    tr_maps = tarkov_api.Translator("maps")
    en_names = sorted({(tr_maps.en.get(m["name"], ""), map_key[mid]) for mid, m in maps.items()
                       if tr_maps.en.get(m["name"])}, key=lambda x: -len(x[0]))

    found = {"self": [], "map": [], "count": [], "no_page": [], "unknown_name": []}
    skipped = {"no_location": 0, "dev_no_map": 0}
    for qid, task in sorted(tasks.items(), key=lambda kv: tr.en.get(kv[1]["name"], "")):
        name = tr.en.get(task["name"], qid)
        if qid in KNOWN_OK:
            continue
        fix = manual.get(qid, {})
        task = quests.patched_task(task, fix)  # 이미 목표를 고친 퀘스트는 고친 값으로 비교
        fixed = fix.get("from") == "game" and fix.get("maps") is not None

        # 1) dev 자기 자신: 목표 문장에 적힌 맵이 그 목표의 맵 목록에 없음 (예: Counteraction 의 Reserve)
        if not fixed:
            for o in task.get("objectives", []):
                sentence = (o.get("_fix_text") or {}).get("original") or tr.en.get(o.get("description"), "")
                # 맵이 아닌데 맵 이름이 들어간 표현: 아이템 이름·'X 제외'·'X 로 가는 통로'·'X 트랜짓 근처'
                sentence = re.sub(r"Hipster Reserve|[Ee]xcluding \w+|leading to [A-Z][\w ]+?(?= on | in |\(|$)|the \w+ transit",
                                  "", sentence)
                sentence = re.sub(r"\btransit\b.*", "", sentence)  # '…에서 …로 트랜짓' 의 도착 맵은 목표 맵이 아님
                said = set()
                for en_name, key in en_names:
                    if re.search(rf"\b{re.escape(en_name)}\b", sentence):
                        said.add(key)
                        sentence = sentence.replace(en_name, "")
                have = {map_key[mid] for mid in (o.get("maps") or []) if mid in map_key}
                if said - have and have:  # 목표에 맵이 아예 없으면(아무 데서나) 넘김
                    found["self"].append({"id": qid, "name": name, "missing": sorted(said - have),
                                          "have": sorted(have), "text": tr.en.get(o.get("description"), "")})

        text = cache.get(titles.get(qid))
        if not text:
            found["no_page"].append({"id": qid, "name": name, "wiki": titles.get(qid)})
            continue
        if fixed:
            dev = {SAME_MAP.get(k, k) for k in fix["maps"]}
        else:
            ids = ([task["map"]] if task.get("map") else []) + [mid for o in task.get("objectives", []) for mid in (o.get("maps") or [])]
            dev = {map_key[mid] for mid in ids if mid in map_key}

        # 2) 위키 정보상자 맵 ↔ dev 맵. 위키가 비었거나, dev 맵이 없는(건네주기·아무 데서나) 퀘스트는 비교 못 함
        #    위키가 dev 보다 적게 적은 건 흔한 표기 차이라 넘기고, 위키에만 있는 맵 / 하나도 안 겹침만 보여 줌
        wiki = wiki_locations(text)
        if wiki is None or wiki == "any":
            skipped["no_location"] += 1
        elif not dev:
            skipped["dev_no_map"] += 1
        else:
            wiki -= IGNORE_WIKI
            odd = sorted(wiki - known_keys)
            if odd:
                found["unknown_name"].append({"id": qid, "name": name, "odd": odd})
            wiki &= known_keys
            if wiki and (wiki - dev or not wiki & dev):
                found["map"].append({"id": qid, "name": name, "dev": sorted(dev), "wiki": sorted(wiki),
                                     "only_wiki": sorted(wiki - dev), "fixed": fixed})

        objectives_text = wiki_objectives(text)
        numbers = {n.replace(",", "") for n in re.findall(r"\d[\d,]*", objectives_text)}
        for o in task.get("objectives", []):
            count = o.get("count") or 1
            if count > 1 and objectives_text and str(count) not in numbers:
                found["count"].append({"id": qid, "name": name, "dev_count": count,
                                       "dev_text": tr.en.get(o.get("description"), ""),
                                       "wiki_numbers": sorted(numbers, key=lambda n: int(n))})

    titles_ko = {
        "self": "dev 목표 문장의 맵이 dev 맵 목록에 없음 (dev 자체 모순)",
        "map": "위키에만 있는 맵 / 하나도 안 겹침 (위키 정보상자 ↔ dev)",
        "count": "dev 개수가 위키 목표에 안 나옴",
        "unknown_name": "위키 맵 이름을 모름 (도구 이름표 보강 필요)", "no_page": "위키 쪽을 못 찾음",
    }
    print(f"dev 퀘스트 {len(tasks)}개 비교 (위키 원문 cache/wiki_quests.json)")
    print(f"확인 끝나 뺀 것(KNOWN_OK) {len(KNOWN_OK)}개")
    print(f"맵 비교 못 함: 위키 location 비었음 {skipped['no_location']}개, dev 맵 없음 {skipped['dev_no_map']}개\n")
    for kind, rows in found.items():
        print(f"== {titles_ko[kind]}: {len(rows)}개")
        for r in rows:
            if kind == "self":
                print(f"  {r['name']}  문장에만 {r['missing']}  목록 {r['have']}\n      \"{r['text']}\"")
            elif kind == "count":
                print(f"  {r['name']}  dev {r['dev_count']}  위키 숫자 {', '.join(r['wiki_numbers']) or '없음'}\n      dev \"{r['dev_text']}\"")
            elif kind == "no_page":
                print(f"  {r['name']}  ({r['wiki']})")
            elif kind == "unknown_name":
                print(f"  {r['name']}  {r['odd']}")
            else:
                tag = "  [보정됨]" if r.get("fixed") else ""
                print(f"  {r['name']}  위키에만 {r['only_wiki'] or '-'}  dev {r['dev']}  위키 {r['wiki']}{tag}")
        print()
    if json_out:
        Path(json_out).write_text(json.dumps(found, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())

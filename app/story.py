"""스토리 챕터 데이터를 읽어서 화면용으로 만드는 파일.

챕터 데이터: data/story/<챕터>.json  (출처: 공식 위키, CC BY-NC-SA → data/story/README.md)
"""

import json
from pathlib import Path

from app import i18n, paths, tarkov_api

STORY_DIR = paths.DATA_DIR / "story"

# 화면에 보여줄 챕터 순서 (데이터 파일이 있는 것만 나옴)
CHAPTER_ORDER = [
    "tour", "falling-skies", "batya", "the-unheard", "blue-fire",
    "they-are-already-here", "accidental-witness", "the-labyrinth", "boreas", "the-ticket",
]


def _map_names() -> dict:
    """지도 구분값(예: customs) → 한국어 이름"""
    tarkov_api.update_all()
    tr = tarkov_api.Translator("maps")
    return {m["normalizedName"]: tr(m["name"]) for m in tarkov_api.load("maps")["maps"].values()}


def _item_id_finder():
    """아이템 이름(한글·영어) → 아이템 id. 스토리 준비물에 아이콘을 붙이려고 씀"""
    import re
    from app import tarkov_api

    tarkov_api.update_all()
    tr = tarkov_api.Translator("items", lang="ko")  # 스토리 글이 한국어라 한국어·영어 이름 둘 다로 찾음
    by_name = {}
    for table in (tr.en, tr.main):
        for key, name in table.items():
            if key.endswith(" Name") and name:
                by_name.setdefault(name.strip().lower(), key[:-len(" Name")])
    # 퀘스트 전용 아이템 (타란의 엽서 등): tasks 데이터의 questItems
    task_tr = tarkov_api.Translator("tasks", lang="ko")
    for item_id, item in (tarkov_api.load("tasks").get("questItems") or {}).items():
        key = item.get("name", "")
        for name in (task_tr.en.get(key), task_tr.main.get(key)):
            if name:
                by_name.setdefault(name.strip().lower(), item_id)

    def find(text: str) -> list[str]:
        # "Taran's postcard (타란의 엽서)" / "충전식 배터리 (Rechargeable battery)" → 앞·괄호 안 둘 다 시도
        m = re.match(r"^(.*?)\s*\(([^)]*)\)\s*$", text)
        for candidate in ([m.group(1), m.group(2)] if m else []) + [text]:
            item_id = by_name.get(candidate.strip().lower())
            if item_id:
                return [item_id]
        return []

    find.quest_items = set((tarkov_api.load("tasks").get("questItems") or {}).keys())
    return find


def _add_item_ids(node, find) -> None:
    """챕터 안의 모든 '제출' 준비물에 아이템 id 를 붙임 (찾으면).
    퀘스트 아이템(진행 중 그 자리에서 줍는 물건)이면 quest_item 표시 → '먹을 것'·'뭘 모아 올까?'에서 뺌.
    tarkov.dev 퀘스트 아이템 목록에 없는 고유 물건(보예보다의 녹음기 등)은 JSON 에 quest_item 을 직접 적음"""
    if isinstance(node, dict):
        if node.get("kind") == "제출" and node.get("text") and "ids" not in node:
            ids = find(node["text"])
            if ids:
                node["ids"] = ids
                if ids[0] in getattr(find, "quest_items", set()):
                    node["quest_item"] = True
        if node.get("quest_item"):
            node["fir"] = False  # 인레이드 표시는 필드에서 주운 물건에만. 퀘스트 아이템은 NPC 와 주고받는 임무 아이템
        for value in node.values():
            _add_item_ids(value, find)
    elif isinstance(node, list):
        for value in node:
            _add_item_ids(value, find)


# 영어 화면: 한국어 글자 칸(title 등) 옆에 같은 이름 + "_en" 칸이 있으면 그것으로 바꿈. 단계는 en(위키 원문)
def _use_english(node) -> None:
    if isinstance(node, dict):
        for key in [k for k in node if f"{k}_en" in node]:
            node[key] = node[f"{key}_en"]
        if "id" in node and "ko" in node and node.get("en"):  # 단계 이름
            node["ko"] = node["en"]
        for value in node.values():
            _use_english(value)
    elif isinstance(node, list):
        for value in node:
            _use_english(value)


def build() -> dict:
    lang = i18n.current_lang()
    names = _map_names()
    try:
        find = _item_id_finder()
    except Exception:
        find = lambda text: []  # 아이템 정보가 없어도 스토리는 그대로
    from app import story_places
    overrides = story_places.step_maps()  # 편집자가 고친 단계별 맵 (여러 맵 체크)
    chapters = []
    for chapter_id in CHAPTER_ORDER:
        path = STORY_DIR / f"{chapter_id}.json"
        if not path.exists():
            continue
        chapter = json.loads(path.read_text(encoding="utf-8"))
        # sections 안에는 일반 구간과 "갈림길"(choice) 항목이 섞여 있을 수 있음
        # map 은 하나("shoreline") 또는 여러 개(["shoreline", "lighthouse"]) 가능
        def with_names(item):
            keys = item.get("map")
            keys = keys if isinstance(keys, list) else ([keys] if keys else [])
            item["map_names"] = [names[k] for k in keys if names.get(k)]
            item["map_name"] = " / ".join(item["map_names"]) or None

        for section in chapter["sections"]:
            with_names(section)
            for step in section.get("steps", []):
                if step.get("id") in overrides:
                    step["map"] = overrides[step["id"]]
                with_names(step)
        _add_item_ids(chapter, find)  # 아이콘은 원래(한국어) 글자로 찾은 뒤
        if lang != "ko":
            _use_english(chapter)  # 그다음 글자만 영어로
        chapters.append(chapter)

    ready = {c["id"] for c in chapters}
    return {
        "chapters": chapters,
        "coming": [c for c in CHAPTER_ORDER if c not in ready],  # 아직 데이터 없는 챕터
    }

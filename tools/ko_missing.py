r"""한국어 화면에서 영어로 나오는 퀘스트 목표 찾기 (개발용).

게임 공식 한국어도 없고 우리 번역(data/ko_overrides.json)도 없는 목표,
또는 우리 번역은 있지만 tarkov.dev 영어 원문이 바뀌어서 쓸 수 없게 된 목표를 보여준다.
→ 찾은 것은 data/ko_overrides.json 에 {"en": 지금 원문, "ko": 번역} 으로 넣는다 (규칙: data/translation_rules.md)

사용: .venv\Scripts\python.exe -X utf8 tools\ko_missing.py [--all]
  (기본은 진행 중인 퀘스트만, --all 은 모든 퀘스트)
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import quest_log, quests, tarkov_api  # noqa: E402

HANGUL = re.compile("[가-힣]")


def main() -> int:
    tarkov_api.update_all()
    tasks = tarkov_api.load("tasks")["tasks"]
    tr = tarkov_api.Translator("tasks", lang="ko")
    overrides = quests._load_overrides()
    only = None
    if "--all" not in sys.argv:
        status = quest_log.current_quest_status(quest_log.last_prestige())
        only = {qid for qid, info in status.items() if info["status"] == "진행 중"}
    missing, stale = [], []
    for qid, task in tasks.items():
        if only is not None and qid not in only:
            continue
        for o in task.get("objectives", []):
            key = o["description"]
            if HANGUL.search(tr.main.get(key) or ""):
                continue  # 게임 공식 한국어 있음
            en = tr.en.get(key) or key
            ours = overrides.get(key)
            if ours and ours.get("en") == en:
                continue  # 우리 번역 있음
            (stale if ours else missing).append((tr(task["name"]), key, en, ours))
    for name, key, en, _ in missing:
        print(f"[번역 없음] {name} | {key} | {en}")
    for name, key, en, ours in stale:
        print(f"[원문 바뀜] {name} | {key} | 지금: {en} | 번역할 때: {ours.get('en')}")
    print(f"\n번역 없음 {len(missing)}개, 원문 바뀜 {len(stale)}개")
    return 1 if missing or stale else 0


if __name__ == "__main__":
    sys.exit(main())

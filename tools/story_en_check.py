r"""스토리 영어 칸(_en) 검사.

1) 각 data/story/*.json 에서 "_en" 칸을 모두 빼면 마지막 커밋(HEAD)과 똑같아야 함 (다른 건 안 바꿨는지)
2) 한국어가 든 글자 칸(title·tip·caption·text·reward·requirement·question·label·desc·blocked_note)마다
   같은 이름 + "_en" 칸이 있어야 하고, 영어 칸에는 한글이 없어야 함

사용: .venv\Scripts\python.exe -X utf8 tools\story_en_check.py
"""

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STORY = ROOT / "data" / "story"
KO = re.compile("[가-힣]")
TEXT_KEYS = {"title", "tip", "caption", "text", "reward", "requirement", "question", "label", "desc", "blocked_note"}


def strip_en(node):
    if isinstance(node, dict):
        return {k: strip_en(v) for k, v in node.items() if not k.endswith("_en")}
    if isinstance(node, list):
        return [strip_en(v) for v in node]
    return node


def walk(node, where, problems, counts):
    if isinstance(node, dict):
        for key, value in node.items():
            if key in TEXT_KEYS and isinstance(value, str) and KO.search(value):
                counts["need"] += 1
                en = node.get(f"{key}_en")
                if not isinstance(en, str) or not en.strip():
                    problems.append(f"{where} > {key}: 영어 칸({key}_en) 없음 — {value[:40]}")
                elif KO.search(en):
                    problems.append(f"{where} > {key}_en: 한글이 들어 있음 — {en[:40]}")
                else:
                    counts["done"] += 1
            if key.endswith("_en") and key[:-3] not in node:
                problems.append(f"{where} > {key}: 짝이 되는 한국어 칸({key[:-3]})이 없음")
        for key, value in node.items():
            walk(value, f"{where} > {node.get('id') or key}", problems, counts)
    elif isinstance(node, list):
        for i, value in enumerate(node):
            walk(value, f"{where}[{i}]", problems, counts)


def main() -> int:
    problems, counts = [], {"need": 0, "done": 0}
    for path in sorted(STORY.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        rel = path.relative_to(ROOT).as_posix()
        head = subprocess.run(["git", "show", f"HEAD:{rel}"], cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
        if head.returncode == 0 and strip_en(data) != json.loads(head.stdout):
            problems.append(f"{rel}: '_en' 칸 말고 다른 내용이 바뀜 (원래 칸·순서·값은 그대로 둬야 함)")
        walk(data, path.stem, problems, counts)
    print(f"영어 칸: {counts['done']} / {counts['need']}")
    for p in problems[:80]:
        print("[!]", p)
    if len(problems) > 80:
        print(f"... 외 {len(problems) - 80}개")
    print("문제 없음" if not problems else f"문제 {len(problems)}개")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())

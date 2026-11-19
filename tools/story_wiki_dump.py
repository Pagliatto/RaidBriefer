"""스토리 챕터 데이터를 만들 때 쓰는 개발용 도구 (프로그램 실행과는 상관없음).

공식 위키에서 챕터 하나의 목표 목록, 가이드 원문, 사진 주소를 모아 파일로 저장한다.
그걸 보고 data/story/<챕터>.json 을 한글로 정리한다.

사용: .venv\\Scripts\\python.exe tools\\story_wiki_dump.py "Falling Skies" 저장할폴더
"""

import json
import re
import sys
from pathlib import Path

import requests

API = "https://escapefromtarkov.fandom.com/api.php"
HEADERS = {"User-Agent": "RaidBriefer/0.1 (unofficial fan tool)"}


def _get(params):
    params = {**params, "format": "json", "redirects": 1}
    return requests.get(API, params=params, headers=HEADERS, timeout=30).json()


def section_text(page: str, title: str) -> str:
    secs = _get({"action": "parse", "page": page, "prop": "sections"})["parse"]["sections"]
    idx = next((s["index"] for s in secs if s["line"].strip().lower() == title.lower()), None)
    if not idx:
        return ""
    return _get({"action": "parse", "page": page, "prop": "wikitext", "section": idx})["parse"]["wikitext"]["*"]


def objectives(wikitext: str) -> list:
    out = []
    for line in wikitext.splitlines():
        m = re.match(r"^(\*+)\s*(.+)", line)
        if not m:
            continue
        text = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]+)\]\]", r"\1", m[2])
        text = re.sub(r"'''?|<[^>]+>|\{\{[^}]*\}\}", "", text).strip()
        out.append({"level": len(m[1]), "en": text})
    return out


def image_urls(names: list) -> dict:
    urls = {}
    for i in range(0, len(names), 40):  # 위키 API는 한 번에 50개까지
        chunk = names[i:i + 40]
        data = _get({"action": "query", "titles": "|".join("File:" + n for n in chunk),
                     "prop": "imageinfo", "iiprop": "url", "iiurlwidth": 480})
        for p in data["query"]["pages"].values():
            info = (p.get("imageinfo") or [{}])[0]
            urls[p["title"][5:]] = {"thumb": info.get("thumburl"), "full": info.get("url"),
                                    "source": info.get("descriptionurl")}
    return urls


def main():
    page, out_dir = sys.argv[1], Path(sys.argv[2])
    out_dir.mkdir(parents=True, exist_ok=True)
    objs = objectives(section_text(page, "Objectives"))
    guide = section_text(page, "Guide")
    names = sorted(set(m.strip().replace("_", " ") for m in re.findall(r"File:([^|\]\n]+\.(?:png|jpg|jpeg|gif|webp))", guide, re.I)))
    slug = re.sub(r"[^a-z0-9]+", "-", page.lower()).strip("-")
    (out_dir / f"{slug}_objectives.json").write_text(json.dumps(objs, ensure_ascii=False, indent=1), encoding="utf-8")
    (out_dir / f"{slug}_guide.txt").write_text(guide, encoding="utf-8")
    (out_dir / f"{slug}_images.json").write_text(json.dumps(image_urls(names), indent=1), encoding="utf-8")
    print(f"{page}: 목표 {len(objs)}개, 가이드 {len(guide)}자, 사진 {len(names)}장 → {out_dir}")


if __name__ == "__main__":
    main()

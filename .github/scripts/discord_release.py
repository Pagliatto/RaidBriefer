"""새 버전(릴리스)을 디스코드 공지 채널에 카드(embed)로 알린다.

깃허브 Actions(.github/workflows/discord-release.yml)가 실행한다.
'바뀐 점'은 data/release_notes.json 의 그 버전 ko/en 줄을 그대로 쓴다.

환경 변수
  WEBHOOK_NOTICE   공지 채널 웹훅 (깃허브 Secrets, 릴리스 이벤트일 때만 씀)
  WEBHOOK_TEST     시험용 관리자 채널 웹훅 (손으로 실행할 때만 씀)
  GITHUB_EVENT_NAME  깃허브가 자동으로 넣음 (release / workflow_dispatch)
  TAG              릴리스 태그 (예: v1.3.0)
  REPO             저장소 이름 (예: Pagliatto/RaidBriefer)
  RELEASE_URL      릴리스 페이지 주소 (없으면 태그로 만듦)
  RELEASE_BODY     release_notes.json 에 그 버전이 없을 때만 쓰는 릴리스 설명
  PUBLISHED_AT     릴리스 시각 (ISO 8601, 카드 아래 시각)

내 PC 에서 미리 보기:  python .github/scripts/discord_release.py --dry-run
  (보내지 않고 카드 내용(JSON)만 출력)
"""
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NOTES = ROOT / "data" / "release_notes.json"

COLOR = 0xE8A33D  # 카드 왼쪽 세로줄 색 (호박색)
DESC_LIMIT = 4096  # 디스코드 카드 본문 최대 글자 수


def logo_url(repo):
    return f"https://raw.githubusercontent.com/{repo}/main/docs/logo/RaidBriefer-A-1024.png"


TAG_RE = re.compile(r"^[vV]?\d+(\.\d+){1,3}$")  # v1.3.0 / 1.3.0 같은 모양만
TAG_MAX = 32
URL_MAX = 300


def _lines(value):
    """문자열 목록만 받음. 다른 모양(null·문자열 하나·숫자)이면 빈 목록."""
    if not isinstance(value, list):
        return []
    return [x.strip() for x in value if isinstance(x, str) and x.strip()]


def load_notes(version):
    """그 버전의 (ko 줄, en 줄). 없거나 파일이 이상하면 ([], [])."""
    try:
        data = json.loads(NOTES.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [], []
    entry = data.get(version) if isinstance(data, dict) else None
    if not isinstance(entry, dict):
        return [], []
    return _lines(entry.get("ko")), _lines(entry.get("en"))


def bullets(lines):
    return "\n".join(f"• {line}" for line in lines)


def build_payload(env):
    repo = env.get("REPO") or "Pagliatto/RaidBriefer"
    tag = (env.get("TAG") or "").strip()
    if len(tag) > TAG_MAX or not TAG_RE.match(tag):
        raise SystemExit(f"TAG 모양이 이상함 (예: v1.3.0): {tag[:TAG_MAX]!r}")
    version = tag[1:] if tag[:1] in ("v", "V") else tag
    url = (env.get("RELEASE_URL") or "").strip()
    if not url.startswith("https://github.com/") or len(url) > URL_MAX:
        url = f"https://github.com/{repo}/releases/tag/{tag}"

    ko, en = load_notes(version)
    parts = []
    if ko or en:
        if ko:
            parts.append("**🇰🇷 바뀐 점**\n" + bullets(ko))
        if en:
            parts.append("**🇺🇸 What's new**\n" + bullets(en))
    else:
        # release_notes.json 에 없으면 깃허브 릴리스 설명이라도 보여 줌
        body = (env.get("RELEASE_BODY") or "").strip()
        if body:
            parts.append(body)

    tail = (f"**[⬇ 다운로드 / Download]({url})**\n"
            "프로그램을 켜면 자동 업데이트 안내가 떠요 · The app offers the update when you open it")

    # 본문이 너무 길면 바뀐 점 쪽을 자르고 다운로드 안내는 꼭 남김
    room = DESC_LIMIT - len(tail) - 2
    head = "\n\n".join(parts)
    if len(head) > room:
        head = head[: max(0, room - 1)].rstrip() + "…"
    description = (head + "\n\n" + tail) if head else tail

    embed = {
        "title": f"🚀 RaidBriefer {tag}",
        "url": url,
        "description": description,
        "color": COLOR,
        "thumbnail": {"url": logo_url(repo)},
        "footer": {"text": "Pagliatto Lab · RaidBriefer"},
    }
    published = (env.get("PUBLISHED_AT") or "").strip()
    if published:
        embed["timestamp"] = published

    return {
        "avatar_url": logo_url(repo),
        "embeds": [embed],
        # 공지에서 @everyone 같은 멘션이 울리지 않게
        "allowed_mentions": {"parse": []},
    }


def send(webhook, payload):
    req = urllib.request.Request(
        webhook,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            # 디스코드는 기본 파이썬 이름(User-Agent)을 막는 경우가 있음
            "User-Agent": "RaidBriefer-release-notice (github actions)",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            print(f"보냄: HTTP {res.status}")
    except urllib.error.HTTPError as e:
        # 웹훅 주소는 출력하지 않음
        detail = e.read().decode("utf-8", "replace")[:500]
        raise SystemExit(f"디스코드가 거절함: HTTP {e.code} {detail}")


def main():
    payload = build_payload(os.environ)
    if "--dry-run" in sys.argv:
        sys.stdout.reconfigure(encoding="utf-8")
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    # 릴리스면 공지, 그 밖(손으로 실행)은 시험 채널. 비어 있어도 다른 쪽으로 넘어가지 않음
    event = os.environ.get("GITHUB_EVENT_NAME", "")
    name = "WEBHOOK_NOTICE" if event == "release" else "WEBHOOK_TEST"
    webhook = (os.environ.get(name) or "").strip()
    if not webhook:
        raise SystemExit(f"{name} 이 비어 있음 (깃허브 Secrets 확인, 이벤트: {event or '없음'})")
    send(webhook, payload)


if __name__ == "__main__":
    main()

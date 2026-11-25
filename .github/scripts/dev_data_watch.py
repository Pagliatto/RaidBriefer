"""tarkov.dev 공개 데이터가 지난번과 달라졌는지 보고, 달라졌으면 디스코드 관리자 채널에 요약을 보낸다.

깃허브 Actions(.github/workflows/dev-data-watch.yml)가 하루 한 번 실행한다.
보는 것: 퀘스트(추가·삭제·이름·목표 글·맵·개수·자리 수)와 지도 표시(탈출구·트랜짓·BTR 정류장).
아이템·은신처는 프로그램이 dev 를 그대로 보여 줘서 따로 할 일이 없으므로 보지 않는다.
비교는 영어 원문으로만 한다 (번역 문구 수정까지 알리면 시끄러움).

지난 상태는 요약본(snapshot.json) 하나로 둔다. Actions 가 전용 가지에 보관한다.

쓰는 법
  python .github/scripts/dev_data_watch.py --snapshot <요약본 경로> [--dry-run] [--from-dir <폴더>]
    --snapshot   지난 요약본을 읽고, 끝나면 새 요약본으로 덮어씀 (없으면 새로 만들고 알림은 안 보냄)
    --dry-run    디스코드로 보내지 않고 보낼 내용만 출력 (요약본은 덮어씀)
    --from-dir   인터넷 대신 폴더의 pve_tasks.json·pve_tasks_en.json·pve_maps.json·pve_maps_en.json 을 읽음 (내 PC 시험용)

환경 변수
  WEBHOOK   알림을 보낼 디스코드 웹훅 (깃허브 Secrets). --dry-run 이 아니고 바뀐 것이 있을 때만 씀

끝날 때 표준 출력 마지막 줄: changed=true / changed=false (Actions 가 요약본을 올릴지 정함)
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

BASE_URL = "https://json.tarkov.dev/pve"
FILES = ("tasks", "tasks_en", "maps", "maps_en")
SNAPSHOT_VERSION = 1
COLOR = 0x6FB3EC      # 카드 왼쪽 세로줄 색 (파랑: 릴리스 공지의 호박색과 구분)
DESC_LIMIT = 4096     # 디스코드 카드 본문 최대 글자 수
LINES_PER_GROUP = 12  # 한 묶음에서 보여 줄 줄 수 (넘으면 '외 N개')
# 퀘스트·맵 수가 지난번의 이 비율 아래로 줄면 dev 쪽 고장으로 보고 요약본을 덮어쓰지 않음
MIN_KEEP_RATIO = 0.7


# ---------- 받기 ----------

def fetch(name):
    req = urllib.request.Request(f"{BASE_URL}/{name}", headers={"User-Agent": "RaidBriefer-dev-data-watch (github actions)"})
    with urllib.request.urlopen(req, timeout=120) as res:
        return json.loads(res.read().decode("utf-8"))["data"]


def load_all(from_dir):
    if from_dir:
        return {name: json.loads((Path(from_dir) / f"pve_{name}.json").read_text(encoding="utf-8")) for name in FILES}
    return {name: fetch(name) for name in FILES}


# ---------- 요약본 만들기 ----------

def _list(value):
    return value if isinstance(value, list) else []


def _text(table, key):
    """번역 표에서 영어 글을 찾음. 없으면 열쇠 그대로."""
    value = table.get(key) if isinstance(table, dict) else None
    return value if isinstance(value, str) and value else str(key or "")


def build_snapshot(data):
    tasks = data["tasks"].get("tasks") if isinstance(data["tasks"], dict) else None
    maps = data["maps"].get("maps") if isinstance(data["maps"], dict) else None
    if not isinstance(tasks, dict) or not isinstance(maps, dict) or not tasks or not maps:
        raise SystemExit("dev 데이터 모양이 예상과 다름 (tasks·maps 가 비었거나 없음) → 요약본을 덮어쓰지 않음")
    task_en, map_en = data["tasks_en"], data["maps_en"]
    map_key = {m.get("id"): m.get("normalizedName") or m.get("id") for m in maps.values() if isinstance(m, dict)}

    quests = {}
    for task in tasks.values():
        if not isinstance(task, dict) or not task.get("id"):
            continue
        objectives = []
        for o in _list(task.get("objectives")):
            if not isinstance(o, dict):
                continue
            objectives.append({
                "id": str(o.get("id") or ""),
                "text": _text(task_en, o.get("description")),
                "type": o.get("type") or "",
                "optional": bool(o.get("optional")),
                "count": o.get("count") if isinstance(o.get("count"), (int, float)) else None,
                "maps": sorted(str(map_key.get(m, m)) for m in _list(o.get("maps"))),
                "spots": len(_list(o.get("zones"))) + len(_list(o.get("possibleLocations"))),
                "exit": o.get("exitName") or "",
            })
        quests[task["id"]] = {"name": _text(task_en, task.get("name")), "objectives": objectives}

    map_rows = {}
    for m in maps.values():
        if not isinstance(m, dict):
            continue
        key = m.get("normalizedName") or m.get("id")
        if not key:
            continue
        map_rows[key] = {
            "name": _text(map_en, m.get("name")),
            "extracts": sorted(f"{_text(map_en, e.get('name')) or '?'} ({e.get('faction') or '?'})" for e in _list(m.get("extracts")) if isinstance(e, dict)),
            "transits": sorted(_text(map_en, t.get("description")) for t in _list(m.get("transits")) if isinstance(t, dict)),
            "btr": sorted(_text(map_en, s.get("name")) for s in _list(m.get("btrStops")) if isinstance(s, dict)),
        }
    return {"version": SNAPSHOT_VERSION, "quests": quests, "maps": map_rows}


# ---------- 비교 ----------

def _count_diff(old, new):
    """목록 두 개의 (사라진 것, 생긴 것). 같은 이름이 여러 번 있어도 개수로 셈."""
    left, right = list(old), list(new)
    for item in list(left):
        if item in right:
            left.remove(item)
            right.remove(item)
    return left, right


def _by_id(objectives):
    """목표 id → 목표. id 가 비었거나 겹치면 덮어쓰지 않고 몇 번째인지를 붙여 모두 남김."""
    rows, seen = {}, {}
    for goal in _list(objectives):
        key = goal.get("id") or ""
        seen[key] = seen.get(key, 0) + 1
        rows[key if key and seen[key] == 1 else f"{key}#{seen[key]}"] = goal
    return rows


def diff_quest(old, new):
    """한 퀘스트에서 달라진 점을 짧은 말로. 없으면 빈 목록."""
    notes = []
    if old.get("name") != new.get("name"):
        notes.append(f"이름 「{old.get('name')}」 → 「{new.get('name')}」")
    old_goals, new_goals = _by_id(old.get("objectives")), _by_id(new.get("objectives"))
    added = [g for key, g in new_goals.items() if key not in old_goals]
    removed = [g for key, g in old_goals.items() if key not in new_goals]
    if added:
        notes.append(f"목표 추가 {len(added)} ({added[0]['text'][:60]})")
    if removed:
        notes.append(f"목표 삭제 {len(removed)} ({removed[0]['text'][:60]})")
    changed = {"text": 0, "maps": 0, "count": 0, "spots": [], "other": 0}
    for key, new_goal in new_goals.items():
        old_goal = old_goals.get(key)
        if not old_goal:
            continue
        if old_goal.get("text") != new_goal.get("text"):
            changed["text"] += 1
        if old_goal.get("maps") != new_goal.get("maps"):
            changed["maps"] += 1
        if old_goal.get("count") != new_goal.get("count"):
            changed["count"] += 1
        if old_goal.get("spots") != new_goal.get("spots"):
            changed["spots"].append(f"{old_goal.get('spots')}→{new_goal.get('spots')}")
        if any(old_goal.get(k) != new_goal.get(k) for k in ("type", "optional", "exit")):
            changed["other"] += 1
    if changed["text"]:
        notes.append(f"목표 글 바뀜 {changed['text']}")
    if changed["maps"]:
        notes.append(f"목표 맵 바뀜 {changed['maps']}")
    if changed["count"]:
        notes.append(f"개수 바뀜 {changed['count']}")
    if changed["spots"]:
        notes.append("자리 수 " + ", ".join(changed["spots"][:4]))
    if changed["other"]:
        notes.append(f"종류·선택·탈출구 바뀜 {changed['other']}")
    return notes


def diff(old, new):
    """달라진 점을 묶음별 줄 목록으로: [(제목, [줄, ...]), ...]. 없으면 빈 목록."""
    groups = []
    old_q, new_q = old.get("quests") or {}, new.get("quests") or {}
    added = [new_q[k]["name"] for k in new_q if k not in old_q]
    removed = [old_q[k]["name"] for k in old_q if k not in new_q]
    edited = []
    for key, quest in new_q.items():
        if key in old_q:
            notes = diff_quest(old_q[key], quest)
            if notes:
                edited.append(f"{quest['name']}: " + " · ".join(notes))
    if added:
        groups.append(("새 퀘스트", sorted(added)))
    if removed:
        groups.append(("사라진 퀘스트", sorted(removed)))
    if edited:
        groups.append(("바뀐 퀘스트", sorted(edited)))

    old_m, new_m = old.get("maps") or {}, new.get("maps") or {}
    map_lines = []
    for key in sorted(set(old_m) | set(new_m)):
        if key not in old_m:
            map_lines.append(f"{new_m[key]['name']}: 새 맵")
            continue
        if key not in new_m:
            map_lines.append(f"{old_m[key]['name']}: 맵이 사라짐")
            continue
        for field, label in (("extracts", "탈출구"), ("transits", "트랜짓"), ("btr", "BTR 정류장")):
            gone, came = _count_diff(_list(old_m[key].get(field)), _list(new_m[key].get(field)))
            if came:
                map_lines.append(f"{new_m[key]['name']}: {label} 추가 {len(came)} ({', '.join(came[:3])})")
            if gone:
                map_lines.append(f"{new_m[key]['name']}: {label} 삭제 {len(gone)} ({', '.join(gone[:3])})")
    if map_lines:
        groups.append(("지도 표시", map_lines))
    return groups


def looks_broken(old, new):
    """dev 가 잠깐 빈 데이터를 내보낸 것 같으면 이유를 돌려줌 (그날은 알리기만 하고 요약본은 그대로 둠)."""
    for field, label in (("quests", "퀘스트"), ("maps", "맵")):
        before, after = len(old.get(field) or {}), len(new.get(field) or {})
        if before and after < before * MIN_KEEP_RATIO:
            return f"{label} 수가 {before} → {after} 로 크게 줄었어요. dev 쪽 문제일 수 있어 기준은 그대로 둡니다."
    return ""


# ---------- 알림 ----------

def build_payload(groups, warning=""):
    parts = []
    if warning:
        parts.append(f"⚠ {warning}")
    for title, lines in groups:
        shown = [f"• {line[:300]}" for line in lines[:LINES_PER_GROUP]]
        if len(lines) > LINES_PER_GROUP:
            shown.append(f"…외 {len(lines) - LINES_PER_GROUP}개")
        parts.append(f"**{title} {len(lines)}**\n" + "\n".join(shown))
    description = "\n\n".join(parts)
    if len(description) > DESC_LIMIT:
        description = description[: DESC_LIMIT - 1].rstrip() + "…"
    return {
        "embeds": [{
            "title": "tarkov.dev 데이터가 바뀌었어요",
            "description": description,
            "color": COLOR,
            "footer": {"text": "RaidBriefer · dev data watch (PvE)"},
        }],
        # @everyone 같은 멘션이 울리지 않게 (dev 글에 섞여 있어도)
        "allowed_mentions": {"parse": []},
    }


def send(webhook, payload):
    req = urllib.request.Request(
        webhook,
        data=json.dumps(payload).encode("utf-8"),
        # 디스코드는 기본 파이썬 이름(User-Agent)을 막는 경우가 있음
        headers={"Content-Type": "application/json", "User-Agent": "RaidBriefer-dev-data-watch (github actions)"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            print(f"보냄: HTTP {res.status}")
    except urllib.error.HTTPError as e:
        # 웹훅 주소는 출력하지 않음
        raise SystemExit(f"디스코드가 거절함: HTTP {e.code} {e.read().decode('utf-8', 'replace')[:500]}")


def read_snapshot(path):
    """지난 요약본. 없거나 읽을 수 없거나 모양이 다르면 None (그날은 기준만 새로 만듦)."""
    try:
        old = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(old, dict) or old.get("version") != SNAPSHOT_VERSION or not _valid(old):
        return None
    return old


def _valid(snapshot):
    """요약본이 build_snapshot 이 만든 모양인지 (손으로 고쳤거나 깨진 파일이면 비교하다 죽지 않게)."""
    quests, maps = snapshot.get("quests"), snapshot.get("maps")
    if not isinstance(quests, dict) or not isinstance(maps, dict):
        return False
    for quest in quests.values():
        if not isinstance(quest, dict) or not isinstance(quest.get("name"), str) or not isinstance(quest.get("objectives"), list):
            return False
        for goal in quest["objectives"]:
            if not isinstance(goal, dict) or not isinstance(goal.get("id"), str) or not isinstance(goal.get("text"), str):
                return False
    for row in maps.values():
        if not isinstance(row, dict) or not isinstance(row.get("name"), str):
            return False
        if any(not isinstance(row.get(field), list) or any(not isinstance(x, str) for x in row[field])
               for field in ("extracts", "transits", "btr")):
            return False
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--from-dir")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    path = Path(args.snapshot)
    new = build_snapshot(load_all(args.from_dir))
    old = read_snapshot(path)

    def save():
        path.parent.mkdir(parents=True, exist_ok=True)
        # 줄을 나눠 저장: 가지 기록(git diff)에서 무엇이 바뀌었는지 읽을 수 있게
        path.write_text(json.dumps(new, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")

    if old is None:
        save()
        print(f"기준 요약본을 새로 만듦: 퀘스트 {len(new['quests'])}개, 맵 {len(new['maps'])}개 (알림 없음)")
        print("changed=true")
        return

    warning = looks_broken(old, new)
    groups = [] if warning else diff(old, new)
    if not warning and not groups:
        print("바뀐 것 없음")
        print("changed=false")
        return

    payload = build_payload(groups, warning)
    if args.dry_run:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        webhook = (os.environ.get("WEBHOOK") or "").strip()
        if not webhook:
            raise SystemExit("WEBHOOK 이 비어 있음 (깃허브 Secrets 확인)")
        send(webhook, payload)
    # 알림을 보낸 뒤에만 기준을 바꿈 (보내기에 실패하면 내일 다시 알림). 고장으로 보이는 날은 기준을 그대로
    if warning:
        print("changed=false")
        return
    save()
    print("changed=true")


if __name__ == "__main__":
    main()

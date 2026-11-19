"""게임 로그(push-notifications)를 읽어서 퀘스트 상태를 알아내는 파일.

로그에 남는 퀘스트 알림 (new_message의 type)
- 10 = 퀘스트 시작
- 11 = 퀘스트 실패
- 12 = 퀘스트 완료

같은 퀘스트에 여러 기록이 있으면 '가장 마지막 기록'이 지금 상태다.
(예: 실패(11) 후 다시 시작(10)하면 → 진행 중)
"""

import json
import re
from datetime import datetime
from pathlib import Path

from app import i18n, paths, settings


# ---------- 가장 최근 프레스티지(초기화) 찾기 ----------
# 규칙 (어느 PC에서나 같음):
#   backend 로그에서 PvE 서버(gw-pve)로 보낸 "/client/prestige/obtain" 요청 중 가장 최근 시각.
#   찾지 못하면 None → 남아 있는 로그 전체를 처음부터 읽는다.
# 로그 파일이 많아서, 이미 읽은 파일은 기억해 두고 새로 생기거나 바뀐 파일만 다시 읽는다.
PRESTIGE_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*Request HTTPS.*https://gw-pve[^/]*/client/prestige/obtain")
_prestige_cache: dict[Path, tuple[float, list[datetime]]] = {}


def last_prestige() -> datetime | None:
    found = []
    for path in settings.logs_dir().glob("log_*/*backend_*.log"):
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        cached = _prestige_cache.get(path)
        if not cached or cached[0] != mtime:
            times = []
            try:
                with path.open(encoding="utf-8", errors="replace") as fh:
                    for line in fh:
                        if "prestige/obtain" in line:
                            m = PRESTIGE_RE.match(line)
                            if m:
                                times.append(datetime.strptime(m[1], "%Y-%m-%d %H:%M:%S"))
            except OSError:
                continue
            _prestige_cache[path] = cached = (mtime, times)
        found.extend(cached[1])
    return max(found) if found else None

STATUS_NAMES = {10: "진행 중", 11: "실패", 12: "완료"}

# 일일·주간 반복 과제의 틀 번호 (여러 상인이 같은 번호로 여러 번 줌) → 목록에서 뺀다
# 2026-11-17 로그로 확인: 여러 상인·여러 날짜에 같은 번호로 완료 기록, 보상은 루블 + 문서 아이템
REPEATABLE_TEMPLATES = {
    "616041eb031af660100c9967",
    "61604635c725987e815b1a46",
    "616051e63f96cc089c1cf37f",
    "616052ea3054fc0e2c24ce6e",  # 같은 계열 번호 (처치 과제로 알려짐, 로그에선 아직 못 봄)
}

# tarkov.dev에 아직 없는 퀘스트의 이름표 (직접 적어 둔 것)
MANUAL_QUESTS_FILE = paths.DATA_DIR / "manual_quests.json"


def load_manual_quests() -> dict:
    try:
        data = json.loads(MANUAL_QUESTS_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return {k: v for k, v in data.items() if not k.startswith("_")}


def quest_name(quest_id: str, tasks: dict, tr, manual: dict) -> str:
    """퀘스트 이름: tarkov.dev → 직접 적은 이름표(same_as 포함) → id 순서로 찾는다."""
    if quest_id in tasks:
        return tr(tasks[quest_id]["name"])
    same_as = manual.get(quest_id, {}).get("same_as")
    if same_as in tasks:
        return tr(tasks[same_as]["name"])  # tarkov.dev에는 옛 id로 있는 같은 퀘스트
    if quest_id in manual:
        m = manual[quest_id]
        name = (m.get("name_en") if i18n.current_lang() != "ko" else None) or m["name"]
        return name  # 게임에 나오는 이름 그대로 (직접 적은 이름이라는 표시는 화면의 manual 태그로)
    return i18n.t("server.unknownQuest", id=quest_id)

def quest_name_en(quest_id: str, tasks: dict, tr, manual: dict) -> str | None:
    """퀘스트의 영어 이름 (한국어 화면에서 제목 옆에 작게 보여 줌)"""
    task = tasks.get(quest_id) or tasks.get(manual.get(quest_id, {}).get("same_as"))
    if task:
        return tr.en.get(task["name"])
    return manual.get(quest_id, {}).get("name_en")


# 로그 한 줄의 시작: "2026-11-08 13:32:44.478|..."
LINE_START = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}")


def _read_notifications(log_file: Path):
    """로그 파일 하나에서 'ChatMessageReceived' 알림의 내용(JSON)을 하나씩 꺼낸다."""
    lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
    i = 0
    while i < len(lines):
        if "Got notification | ChatMessageReceived" in lines[i]:
            # 다음 줄부터, 새 로그 줄이 시작되기 전까지가 JSON 내용
            block = []
            i += 1
            while i < len(lines) and not LINE_START.match(lines[i]):
                block.append(lines[i])
                i += 1
            try:
                yield json.loads("\n".join(block))
            except json.JSONDecodeError:
                pass  # 내용이 잘려 있으면 건너뜀
        else:
            i += 1


def log_rev() -> str:
    """퀘스트 기록이 바뀌었는지 알려주는 짧은 표시 (가장 최근 로그 폴더의 알림 로그 크기).
    화면이 2초마다 물어보고, 값이 바뀌면 퀘스트를 새로 불러옴. 파일 크기만 보므로 아주 가벼움"""
    try:
        folders = sorted(settings.logs_dir().glob("log_*"))  # 폴더 이름이 날짜순 (log_2026.11.18_23-13-16_…)
        if not folders:
            return ""
        newest = folders[-1]
        size = sum(f.stat().st_size for f in newest.glob("*push-notifications*.log"))
        return f"{newest.name}:{size}"
    except OSError:
        return ""


def read_quest_events(since: datetime | None = None) -> list[dict]:
    """since(기본: 가장 최근 프레스티지) 이후의 퀘스트 기록을 시간 순서대로 돌려준다.
    프레스티지 기록이 없으면 남아 있는 로그 전체를 읽는다."""
    if since is None:
        since = last_prestige() or datetime.min
    events = {}
    for log_file in settings.logs_dir().glob("log_*/*push-notifications*.log"):
        for note in _read_notifications(log_file):
            message = note.get("message", {})
            kind = message.get("type")
            if kind not in STATUS_NAMES:
                continue
            when = datetime.fromtimestamp(message.get("dt", 0))
            if when < since:
                continue
            quest_id = message.get("templateId", "").split(" ")[0]
            if quest_id in REPEATABLE_TEMPLATES:
                continue  # 반복 과제는 진행에 영향이 없어서 뺌
            # 같은 알림이 두 번 기록될 수 있어서 메시지 고유번호(_id)로 중복 제거
            events[message.get("_id")] = {
                "quest_id": quest_id,
                "type": kind,
                "time": when,
            }
    return sorted(events.values(), key=lambda e: e["time"])


def current_quest_status(since: datetime | None = None) -> dict[str, dict]:
    """퀘스트 id → {상태, 마지막 기록 시각}. 마지막 기록이 이긴다."""
    return status_from_events(read_quest_events(since))


def missing_starts(events: list[dict]) -> set[str]:
    """'시작' 기록 없이 완료·실패만 있는 퀘스트 = 로그 앞부분이 지워졌다는 단서.
    온전한 로그에서는 0개 (2026-11-17 확인). 어느 PC에서나 로그만 보고 같은 기준으로 판단."""
    started = {e["quest_id"] for e in events if e["type"] == 10}
    return {e["quest_id"] for e in events if e["type"] in (11, 12)} - started


def status_from_events(events: list[dict]) -> dict[str, dict]:
    """퀘스트 기록들 → 퀘스트 id → {상태, 마지막 기록 시각}"""
    status = {}
    for event in events:
        status[event["quest_id"]] = {
            "status": STATUS_NAMES[event["type"]],
            "time": event["time"],
        }
    return status


if __name__ == "__main__":
    from app import tarkov_api

    print(tarkov_api.update_all())
    tasks, tr = tarkov_api.get_tasks()
    manual = load_manual_quests()

    status = current_quest_status()
    counts = {}
    for info in status.values():
        counts[info["status"]] = counts.get(info["status"], 0) + 1

    print(f"기준: {last_prestige() or "(프레스티지 기록 없음 → 전체 로그)"} 이후 기록")
    print("요약:", ", ".join(f"{k} {v}개" for k, v in counts.items()))
    print()
    print("[진행 중인 퀘스트]")
    for quest_id, info in sorted(status.items(), key=lambda x: x[1]["time"]):
        if info["status"] == "진행 중":
            name = quest_name(quest_id, tasks, tr, manual)
            print(f"  {name}  (받은 시각 {info['time']:%m-%d %H:%M})")
    print("\n* = tarkov.dev에 없어서 data/manual_quests.json에 직접 적은 이름")

"""신고용 기록 (오류 코드 방법 B, 2026-11-23 사용자, docs/notes/discord.md).

user_data/report.json 에 최근 기록을 모으고, 사람이 읽는 user_data/report.txt 를 같이 쓴다.
통째로 올려도 되게 '안전한 내용만' 적는다: 시각·버전·코드·오류 종류·맵 이름.
오류 원문·경로·사용자 이름·로그 내용은 절대 적지 않는다 (경로나 계정이 섞일 수 있음).

- 예상 못 한 오류 코드: ERR-<우리 파일 이름>-<줄> (예 ERR-quests-412) + 오류 종류(KeyError)
- 흐름: 프로그램 시작·종료, 정상 종료 안 됨, 게임 켜짐/꺼짐, 맵 들어감/나옴, 알림 켜짐/꺼짐
- 강제로 껐다 켜도 남도록 파일에 저장 (알림함은 [확인했어요] 전까지 오류를 보여 줌)
"""

import json
import os
import re
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

from app.paths import USER_DIR

MAX_ENTRIES = 200
JSON_FILE = USER_DIR / "report.json"
TEXT_FILE = USER_DIR / "report.txt"
APP_DIR = Path(__file__).resolve().parent
JS_FILES = frozenset(p.stem for p in (APP_DIR / "static").glob("*.js"))  # 화면 오류 코드에 쓸 수 있는 우리 화면 파일
KINDS = {"start", "stop", "unclean", "game_on", "game_off", "map_in", "map_out", "alert_on", "alert_off", "error"}

# 파일에서 다시 읽을 때 칸마다 허용하는 모양 (손상·손으로 고친 파일의 경로·원문이 report.txt 로 나가지 않게)
FIELD_RULES = {
    "code": re.compile(r"(ERR-(?:js-)?\w{1,40}-\d{1,6}|[A-Z]{2,8}-\d{1,3})"),  # js- = 화면 오류
    "type": re.compile(r"[A-Za-z_]\w{0,59}"),
    "version": re.compile(r"[\w.+-]{1,30}"),
    "map": re.compile(r"[\w -]{1,40}"),  # normalizedName, 없으면 이름(띄어쓰기 가능)
}

_lock = threading.Lock()
_save_lock = threading.Lock()  # 같은 임시 파일을 두 일꾼이 동시에 쓰지 않게
_data = None  # {"entries": [...], "next": 순번, "acked": 순번}
_hold_saves = False  # 기존 파일을 읽지도 옮기지도 못했으면 덮어쓰지 않는다 (잠금이 풀린 뒤 빈 기록으로 덮는 일 방지)


# ---------- 오류 코드 ----------
def _is_ours(filename: str) -> bool:
    try:
        if not os.path.isabs(filename):
            # 배포 exe(PyInstaller)는 경로를 'app\quests.py' 처럼 줄여 저장한다 → 묶음 폴더 기준으로 본다
            filename = os.path.join(str(APP_DIR.parent), filename)
        path = os.path.normcase(os.path.abspath(filename))
    except (TypeError, ValueError):
        return False
    return path.startswith(os.path.normcase(str(APP_DIR)) + os.sep)


def error_code(error: BaseException) -> tuple[str, str]:
    """(코드, 오류 종류). 우리 코드 중 오류에 가장 가까운 줄을 쓴다 (경로·원문은 버림)"""
    place = None
    try:
        for frame in traceback.extract_tb(error.__traceback__):
            if _is_ours(frame.filename):
                place = (Path(frame.filename).stem, frame.lineno or 0)
    except Exception:
        place = None
    name = type(error).__name__
    if not name.isidentifier():
        name = "Error"
    module, line = place if place else ("unknown", 0)
    return f"ERR-{module}-{line}", name


# ---------- 저장 ----------
def _now() -> float:
    return time.time()


def _valid_time(value) -> bool:
    # 1970년 ~ 2100년 안의 숫자만 (손으로 고친 파일·이상한 값이 날짜 그리기를 망가뜨리지 않게)
    return isinstance(value, (int, float)) and not isinstance(value, bool) and 0 < value < 4102444800


def _keep_broken() -> bool:
    """읽을 수 없는 report.json 은 지우지 않고 report.broken.json (있으면 report.broken-2.json …) 으로 남긴다.
    옮기지 못하면 False (그때는 저장을 막아 원본을 덮지 않음)"""
    for number in range(1, 100):
        name = "report.broken.json" if number == 1 else f"report.broken-{number}.json"
        target = JSON_FILE.with_name(name)
        if target.exists():
            continue
        try:
            JSON_FILE.rename(target)  # rename 은 이미 있는 파일을 덮지 않음
            return True
        except FileExistsError:
            continue
        except OSError:
            return False
    return False


def _clean_entry(entry) -> dict | None:
    """저장된 한 줄을 안전한 칸만 남겨 되살린다. 모양이 이상한 줄은 버림"""
    if not (isinstance(entry, dict) and entry.get("kind") in KINDS and _valid_time(entry.get("t"))):
        return None
    clean = {"t": entry["t"], "kind": entry["kind"]}
    if "n" in entry:
        clean["n"] = entry["n"]
    for field, rule in FIELD_RULES.items():
        value = entry.get(field)
        if value is None:
            continue
        if not (isinstance(value, str) and rule.fullmatch(value)):
            return None
        clean[field] = value
    if clean["kind"] == "error" and not ("code" in clean and "type" in clean):
        return None
    return clean


def _load() -> dict:
    """{"entries": [...], "next": 다음 순번, "acked": 확인한 마지막 순번}.
    확인은 시각이 아니라 순번으로 비교한다 (PC 시계가 뒤로 가도 새 오류가 숨지 않게)"""
    global _data, _hold_saves
    if _data is None:
        data = {"entries": [], "next": 1, "acked": 0}
        try:
            saved = json.loads(JSON_FILE.read_text(encoding="utf-8"))
            if not (isinstance(saved, dict) and isinstance(saved.get("entries"), list)):
                raise ValueError("shape")
            entries = [e for e in map(_clean_entry, saved["entries"]) if e][-MAX_ENTRIES:]
            last = 0
            for entry in entries:  # 순번이 없거나 꼬였으면 차례대로 다시 붙인다
                n = entry.get("n")
                entry["n"] = n if isinstance(n, int) and not isinstance(n, bool) and n > last else last + 1
                last = entry["n"]
            acked = saved.get("acked")
            acked = acked if isinstance(acked, int) and not isinstance(acked, bool) else 0
            data = {"entries": entries, "next": last + 1, "acked": min(acked, last)}
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError):
            # 잠겨서 못 읽은 경우에도 이름만 바뀌고 내용은 남음. 옮기지도 못하면 이번 실행은 덮어쓰지 않음
            _hold_saves = not _keep_broken()
        _data = data
    return _data


def _lang() -> str:
    from app import i18n
    try:
        return i18n.current_lang()
    except Exception:
        return i18n.system_lang()


def _line(entry: dict, lang: str) -> str:
    from app import i18n
    stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(entry["t"]))
    kind = entry["kind"]
    if kind == "error":
        text = f"{entry.get('code', '')} ({entry.get('type', '')})"
    elif kind in ("alert_on", "alert_off"):
        text = i18n.t(f"report.{kind}", lang, code=entry.get("code", ""))
    elif kind == "start":
        text = i18n.t("report.start", lang, version=entry.get("version") or "?")
    elif kind == "map_in":
        text = i18n.t("report.map_in", lang, map=entry.get("map") or "?")
    else:
        text = i18n.t(f"report.{kind}", lang)
    return f"{stamp}  {text}"


def header() -> str:
    from app import alerts
    info = alerts.report_info()
    return f"[RaidBriefer report] v{info['version']} / game {info['game_version'] or '-'} / {info['windows'] or '-'}"  # i18n-ok 기록 파일 머리말


def render(limit: int | None = None) -> str:
    with _lock:
        entries = list(_load()["entries"])
    lang = _lang()
    if limit:
        entries = entries[-limit:]
    return "\n".join([header()] + [_line(e, lang) for e in entries]) + "\n"


def _save() -> None:
    """report.json 과 report.txt 를 쓴다. 실패해도 프로그램은 계속 (쓰기 금지 폴더 등)"""
    with _save_lock:
        _save_locked()


def _save_locked() -> None:
    try:
        USER_DIR.mkdir(parents=True, exist_ok=True)
        with _lock:
            current = _load()
            if _hold_saves:
                return
            data = {"entries": list(current["entries"]), "acked": current["acked"]}
        temp = JSON_FILE.with_suffix(".tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        temp.replace(JSON_FILE)
        text = render()
        temp = TEXT_FILE.with_suffix(".txt.tmp")
        temp.write_text(text, encoding="utf-8")
        temp.replace(TEXT_FILE)
    except Exception:
        pass


def add(kind: str, **fields) -> None:
    if kind not in KINDS:
        return
    entry = {"t": _now(), "kind": kind}
    entry.update({k: str(v)[:80] for k, v in fields.items() if v is not None})
    with _lock:
        data = _load()
        entry["n"] = data["next"]
        data["next"] += 1
        data["entries"].append(entry)
        del data["entries"][:-MAX_ENTRIES]
    _save()


def record_error(error: BaseException) -> str:
    """예상 못 한 오류를 기록하고 코드를 돌려준다 (화면 오류 문장에 원문 대신 넣음)"""
    code, name = error_code(error)
    _record(code, name)
    if not getattr(sys, "frozen", False):
        try:
            traceback.print_exception(type(error), error, error.__traceback__)  # 개발 중에는 원문을 콘솔에만
        except Exception:
            pass
    return code


def record_js_error(file: str, line: int, name: str) -> str | None:
    """화면(자바스크립트) 오류: 우리 화면 파일 이름·줄·오류 종류만 받는다 (원문·주소는 받지 않음)"""
    if not (isinstance(file, str) and (file in JS_FILES or file == "unknown")):
        return None
    if not (isinstance(line, int) and not isinstance(line, bool) and 0 <= line <= 999999):
        return None
    if not (isinstance(name, str) and FIELD_RULES["type"].fullmatch(name)):
        return None
    code = f"ERR-js-{file}-{line if file != 'unknown' else 0}"
    _record(code, name)
    return code


def _record(code: str, name: str) -> None:
    try:
        # 같은 오류가 계속 나면(1초마다 도는 일꾼 등) 1분에 한 번만 적는다
        with _lock:
            entries = _load()["entries"]
            recent = any(e["kind"] == "error" and e.get("code") == code and e.get("type") == name and 0 <= _now() - e["t"] < 60 for e in entries[-20:])  # 시계가 뒤로 가면 음수 → 새로 적음
        if not recent:
            add("error", code=code, type=name)
        print(f"[report] {code} ({name})", file=sys.stderr)  # i18n-ok 개발용 콘솔
    except Exception:
        pass


# ---------- 알림함용 ----------
def unacked_errors() -> list[dict]:
    """[확인했어요] 뒤에 생긴 오류 (코드·종류마다 한 줄, 최신 순서). 지난 실행 것도 포함"""
    with _lock:
        data = _load()
        seen, result = set(), []
        for entry in reversed(data["entries"]):
            key = (entry.get("code"), entry.get("type"))
            if entry["kind"] == "error" and entry["n"] > data["acked"] and key not in seen:
                seen.add(key)
                result.append({"code": entry.get("code", ""), "type": entry.get("type", ""), "t": int(entry["t"] * 1000)})
        return result


def ack() -> None:
    with _lock:
        data = _load()
        data["acked"] = data["next"] - 1
    _save()


# ---------- 흐름 기록 ----------
_alerts_on: set | None = None


def note_alerts(codes: list[str]) -> None:
    """알림 코드가 켜지고 꺼질 때만 적는다 (collect 가 2초마다 부름)"""
    global _alerts_on
    now = set(codes)
    with _lock:
        before, _alerts_on = _alerts_on, now
    if before is None:
        before = set()
    for code in sorted(now - before):
        add("alert_on", code=code)
    for code in sorted(before - now):
        add("alert_off", code=code)


def started() -> None:
    """서버 시작: 지난번이 '종료'로 끝나지 않았으면 정상 종료 안 됨을 먼저 적는다"""
    with _lock:
        entries = _load()["entries"]
        unclean = bool(entries) and entries[-1]["kind"] != "stop"
    if unclean:
        add("unclean")
    from app.version import VERSION
    add("start", version=VERSION)


def stopped() -> None:
    add("stop")


def install_hooks() -> None:
    """잡지 못한 오류(뒤에서 도는 일꾼 포함)를 기록"""
    old_thread_hook = threading.excepthook

    def thread_hook(args):
        if args.exc_value is not None and not isinstance(args.exc_value, SystemExit):
            record_error(args.exc_value)
        old_thread_hook(args)

    threading.excepthook = thread_hook


def open_folder() -> bool:
    """탐색기에서 report.txt 를 골라 둔 채로 연다 (사용자가 파일을 끌어다 올릴 수 있게)"""
    try:
        if not TEXT_FILE.exists():
            _save()
        target = TEXT_FILE if TEXT_FILE.exists() else USER_DIR
        if not target.exists():
            return False
        if target == TEXT_FILE:
            # 이름만 쓰면 프로그램·작업 폴더의 같은 이름 파일이 먼저 실행될 수 있어 전체 경로로 (updater 와 같은 방식)
            explorer = os.path.join(os.environ.get("SystemRoot") or r"C:\Windows", "explorer.exe")
            subprocess.Popen(f'"{explorer}" /select,"{target}"')  # 목록으로 넘기면 따옴표 위치가 달라져 다른 폴더가 열림
        else:
            os.startfile(target)  # type: ignore[attr-defined]
        return True
    except Exception:
        return False

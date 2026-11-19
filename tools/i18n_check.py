r"""화면 글자 사전 검사.

1) 코드에서 쓰는 이름표 t("...") 가 사전(app/i18n/ko.json)에 있는지
2) 사전에만 있고 아무 데서도 안 쓰는 이름표
3) 다른 언어 사전(en.json 등)에 빠진 이름표·{값} 이름이 다른 것
4) 아직 코드에 직접 적힌 한국어 (주석 제외) — 파일별 개수, --list 로 줄까지

사용: .venv\Scripts\python.exe tools\i18n_check.py [--list]
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
I18N = ROOT / "app" / "i18n"
CODE = sorted((ROOT / "app" / "static").glob("*.js")) + [ROOT / "app" / "templates" / "index.html"] + sorted((ROOT / "app").glob("*.py"))
KO = re.compile(r"[가-힣]")
USE = re.compile(r"""\bt\(\s*['"]([\w.]+)['"]""")
QUOTED = re.compile(r"""['"]([a-zA-Z]+\.[\w.]+)['"]""")  # t(조건 ? "a.b" : "a.c") 처럼 따로 적힌 이름표
PREFIX = re.compile(r"""\bt\(\s*`([\w.]+)\$\{|\bt\(\s*f["']([\w.]+)\{""")  # t(`prepKind.${...}`), t(f"skills.{...}") → 그 묶음은 모두 씀
# 한국어지만 화면 글자가 아니라 서버·데이터와 맞추는 '코드 값' (나중 단계에서 정리)
CODE_VALUES = re.compile(r'"(제출|열쇠|가져가기|무기|착용|모든 맵|레이드 밖|맵 무관|게임 꺼짐|로비|맵 로딩 중|레이드 중|진행 중|완료|실패)"')


def flatten(d, prefix=""):
    for k, v in d.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            yield from flatten(v, key + ".")
        else:
            yield key, v


def strip_comments(line: str, suffix: str) -> str:
    if suffix == ".js":
        line = re.sub(r"/\*.*?\*/", "", line)
        return re.sub(r"(^|\s)//.*$", "", line)
    if suffix == ".py":
        return re.sub(r"(^|\s)#.*$", "", line)
    return re.sub(r"<!--.*?-->", "", line)


def main() -> int:
    show = "--list" in sys.argv
    base = dict(flatten(json.loads((I18N / "ko.json").read_text(encoding="utf-8"))))
    used, korean, quoted, prefixes = set(), {}, set(), set()
    in_doc = False
    for f in CODE:
        for no, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            used.update(USE.findall(line))
            quoted.update(QUOTED.findall(line))
            prefixes.update(a or b for a, b in PREFIX.findall(line))
            prefixes.update(f"{ns}." for ns in re.findall(r"I18N\.(\w+)", line))  # 사전 묶음을 통째로 쓰는 곳
            s = line.strip()
            if f.suffix == ".py":  # 파이썬 설명 글("""...""")은 화면 글자가 아님
                quotes = line.count('"""')
                if in_doc or quotes:
                    if quotes % 2:
                        in_doc = not in_doc
                    continue
            if s.startswith("print(") and f.suffix == ".py":  # 개발용 콘솔 출력
                continue
            if in_doc or s.startswith(("//", "#", "*", "<!--")) or "i18n-ok" in line:  # i18n-ok = 화면 글자가 아님 (데이터 비교 등)
                continue
            code = CODE_VALUES.sub('""', strip_comments(line, f.suffix))
            if KO.search(code):
                korean.setdefault(f.relative_to(ROOT).as_posix(), []).append((no, s[:110]))
    problems = 0
    missing = sorted(used - set(base))
    if missing:
        problems += len(missing)
        print("[!] 사전에 없는 이름표:", *missing, sep="\n   ")
    seen = used | (quoted & set(base)) | {k for k in base for pre in prefixes if k.startswith(pre)}
    unused = sorted(set(base) - seen)
    if unused:
        print("[i] 안 쓰는 이름표 (HTML 틀의 t('...') 도 포함해서 셈):", *unused, sep="\n   ")
    for other in sorted(I18N.glob("*.json")):
        if other.name == "ko.json":
            continue
        tr = dict(flatten(json.loads(other.read_text(encoding="utf-8"))))
        lack = sorted(set(base) - set(tr))
        if lack:
            print(f"[i] {other.name} 에 아직 번역 안 된 이름표 {len(lack)}개")
        for key in sorted(set(base) & set(tr)):
            if set(re.findall(r"\{(\w+)\}", base[key])) != set(re.findall(r"\{(\w+)\}", tr[key])):
                problems += 1
                print(f"[!] {other.name} {key}: {{값}} 이름이 한국어와 다름")
    total = sum(len(v) for v in korean.values())
    print(f"\n코드에 남은 한국어: {total}줄")
    for name, rows in sorted(korean.items(), key=lambda x: -len(x[1])):
        print(f"  {len(rows):4d}  {name}")
        if show:
            for no, s in rows:
                print(f"        {no}: {s}")
    print("\n문제 없음" if not problems else f"\n문제 {problems}개")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())

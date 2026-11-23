r"""RaidBriefer exe 만들기 → build/RaidBriefer-vX.Y.Z-windows.zip

사용법 (프로젝트 폴더에서):  .venv\Scripts\python.exe tools\build_exe.py

⚠ 내 기록이 섞이지 않게 '커밋된 파일만' 깨끗하게 꺼내서(git archive) 그걸로 만든다.
   user_data(진행 기록·설정), cache, 개발 메모 같은 건 커밋에 없으니 들어갈 수 없음.
   → 고친 내용은 먼저 커밋한 뒤에 만들 것.
"""

import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
SRC = BUILD / "src"
DIST = BUILD / "dist"
NAME = "RaidBriefer"

# 배포 폴더에 들어가면 안 되는 것 (혹시 몰라 한 번 더 검사)
FORBIDDEN = ("user_data", "cache", ".env", "progress.json", "settings.json", "window.json")

README_TXT = """RaidBriefer — Escape from Tarkov PvE 비공식 도우미

실행: RaidBriefer.exe 를 더블클릭하세요.
  - 처음 실행할 때 'Windows의 PC 보호' 창이 뜨면 [추가 정보] → [실행] 을 누르세요.
    (개인이 만든 무료 프로그램이라 유료 인증서가 없어서 뜨는 안내예요)
  - 처음 한 번 '처음 설정' 화면이 떠요.

기록 저장 위치: 이 폴더 안의 user_data 폴더 (폴더째로 옮기면 기록도 같이 옮겨져요)
새 버전으로 바꿀 때: 새 폴더의 내용을 덮어쓰되 user_data 폴더는 그대로 두세요.
업데이트 뒤 프로그램이 안 켜지면: 새로 받아서 이 폴더에 덮어쓰세요. user_data(기록)는 그대로 남아요.

설명·문제 신고: https://github.com/Pagliatto/RaidBriefer
비공식 팬 도구이며 Battlestate Games 와 관련이 없습니다.

--- English ---
Run: double-click RaidBriefer.exe.
  - If "Windows protected your PC" appears, click More info -> Run anyway
    (a free program by an individual, without a paid code-signing certificate).
  - A first-time setup appears once. Language: Settings (gear icon) -> Language.
Records: the user_data folder in this folder (move the whole folder to keep them).
Updates: a notice appears in the program; click "Update now". Keep the user_data folder.
If it won't start after an update: download it again and overwrite this folder. user_data (your records) stays.
Info and bug reports: https://github.com/Pagliatto/RaidBriefer
Unofficial fan tool, not affiliated with Battlestate Games.
"""


def version_file(version: str) -> Path:
    """exe 파일 속성(오른쪽 클릭 → 속성 → 자세히)에 보이는 버전 정보"""
    nums = [int("".join(c for c in part if c.isdigit()) or 0) for part in version.split(".")][:4]
    nums += [0] * (4 - len(nums))
    text = f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={tuple(nums)}, prodvers={tuple(nums)}, mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'Pagliatto'),
      StringStruct('FileDescription', 'RaidBriefer {version} - Escape from Tarkov PvE companion (unofficial)'),
      StringStruct('FileVersion', '{version}'),
      StringStruct('InternalName', 'RaidBriefer'),
      StringStruct('LegalCopyright', 'MIT License - github.com/Pagliatto/RaidBriefer'),
      StringStruct('OriginalFilename', 'RaidBriefer.exe'),
      StringStruct('ProductName', 'RaidBriefer'),
      StringStruct('ProductVersion', '{version}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""
    out = BUILD / "version_info.txt"
    out.write_text(text, encoding="utf-8")
    return out


def run(cmd, **kw):
    print(">", " ".join(str(c) for c in cmd))
    subprocess.run(cmd, check=True, **kw)


def main() -> None:
    dirty = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True).stdout
    if dirty.strip():
        print("[!] 커밋 안 한 변경이 있어요. exe 에는 '마지막 커밋' 내용만 들어가요:\n" + dirty)

    # 1) 깨끗한 복사본
    shutil.rmtree(BUILD, ignore_errors=True)
    SRC.mkdir(parents=True)
    archive = BUILD / "src.tar"
    ref = "HEAD"
    if "--test" in sys.argv:  # 시험용: 커밋하지 않은 변경까지 (새 파일은 git add 해 둬야 들어감). 배포에는 쓰지 말 것
        ref = subprocess.run(["git", "stash", "create"], cwd=ROOT, capture_output=True, text=True).stdout.strip() or "HEAD"
        print("[시험용 빌드] 커밋 안 한 변경 포함:", ref)
    run(["git", "archive", "--format=tar", "-o", str(archive), ref], cwd=ROOT)
    shutil.unpack_archive(archive, SRC)
    archive.unlink()
    if "--version" in sys.argv:  # 시험용: 버전 번호를 바꿔서 빌드 (예: --version 0.1.0 → 자동 업데이트 시험)
        fake = sys.argv[sys.argv.index("--version") + 1]
        (SRC / "app" / "version.py").write_text(f'VERSION = "{fake}"' + chr(10), encoding="utf-8")
        print("[시험용 빌드] 버전:", fake)
    # 개발용 메모는 exe 에 넣지 않음 (데이터 폴더 안의 설명·규칙 문서)
    for memo in SRC.joinpath("data").rglob("*.md"):
        memo.unlink()

    # 2) PyInstaller (폴더 방식: 켜지는 속도가 빠르고 백신 오탐이 적음)
    sep = ";"
    app_version = re.search(r'VERSION = "([^"]+)"', (SRC / "app" / "version.py").read_text(encoding="utf-8")).group(1)
    print("버전:", app_version)
    run([
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
        "--name", NAME, "--windowed",
        "--icon", str(SRC / "app/static/icons/RaidBriefer.ico"),
        "--version-file", str(version_file(app_version)),
        "--add-data", f"{SRC / 'app/templates'}{sep}app/templates",
        "--add-data", f"{SRC / 'app/static'}{sep}app/static",
        "--add-data", f"{SRC / 'app/i18n'}{sep}app/i18n",
        "--add-data", f"{SRC / 'app/update_helper.cmd'}{sep}app",
        "--add-data", f"{SRC / 'data'}{sep}data",
        "--distpath", str(DIST), "--workpath", str(BUILD / "work"), "--specpath", str(BUILD / "work"),
        str(SRC / "RaidBriefer.py"),
    ], cwd=SRC)

    out = DIST / NAME
    for f in ("LICENSE", "NOTICE.md"):
        shutil.copy(SRC / f, out / f)
    (out / "README.txt").write_text(f"RaidBriefer v{app_version}\n\n" + README_TXT, encoding="utf-8-sig")

    # 3) 기록이 섞이지 않았는지 검사
    bad = [p for p in out.rglob("*") if p.name in FORBIDDEN]
    if bad:
        sys.exit(f"[!] 들어가면 안 되는 파일이 있어요: {bad}")

    # 4) 압축 (안에 RaidBriefer 폴더 하나). 받는 사람이 버전을 알 수 있게 이름에 버전을 넣음
    zip_path = BUILD / f"RaidBriefer-v{app_version}-windows.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for p in sorted(out.rglob("*")):
            z.write(p, Path(NAME) / p.relative_to(out))
    print(f"\n완료: {zip_path.name}  ({zip_path.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()

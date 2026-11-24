"""저장소에 올라가면 안 되는 파일이 들어 있는지 검사한다 (자동 검사 checks 에서 실행).

공개 목록은 아래 PUBLIC. 이 PC 전용 메모 같은 이름은 공개 파일에 적지 않고
깃허브 Secret PRIVATE_PATHS (Settings → Secrets and variables → Actions) 에
한 줄로, 공백으로 나눠 넣는다 (Secret 은 밖에서 보낸 PR 에는 전달되지 않음). 걸린 파일 이름만 출력하고 목록 자체는 출력하지 않는다.

모양: `폴더/` = 그 폴더 안 전부, `*` 가 있으면 글자 무늬(fnmatch), 그 밖은 정확히 같은 경로.
"""
import fnmatch
import os
import subprocess
import sys

PUBLIC = [
    ".env", "user_data/", "cache/", ".venv/", "build/", "dist/", "__pycache__/",
    "*.log", "*.lnk", "*.spec", "*.pyc",
]


def patterns():
    private = (os.environ.get("PRIVATE_PATHS") or "").split()
    return PUBLIC + private


def hit(path, pattern):
    if pattern.endswith("/"):
        return path.startswith(pattern) or f"/{pattern}" in f"/{path}"
    if "*" in pattern or "?" in pattern:
        name = path.rsplit("/", 1)[-1]
        return fnmatch.fnmatchcase(path, pattern) or ("/" not in pattern and fnmatch.fnmatchcase(name, pattern))
    return path == pattern


def main():
    files = subprocess.run(["git", "ls-files", "-z"], capture_output=True, check=True).stdout
    tracked = [f for f in files.decode("utf-8", "replace").split("\0") if f]
    pats = patterns()
    bad = sorted({f for f in tracked for p in pats if hit(f, p)})
    if not os.environ.get("PRIVATE_PATHS"):
        print("note: PRIVATE_PATHS is empty, only the public list was checked")
    if bad:
        print("Files that must not be in the repository:")
        for f in bad[:50]:
            print(f"  {f}")
        sys.exit(1)
    print(f"OK: {len(tracked)} files, none forbidden")


if __name__ == "__main__":
    main()

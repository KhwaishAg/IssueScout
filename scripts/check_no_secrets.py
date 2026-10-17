"""P0.6 'Done when' check: no secret appears in git history.

Scans every blob reachable from any ref for private-key blocks and GitHub token
formats, and fails if a file named like a secret (.env, *.pem) was ever committed.
Prints paths and rule names only, never the matched text.
"""

import re
import subprocess
import sys

PATTERNS = {
    "private key block": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |)PRIVATE KEY-----"),
    "github token": re.compile(r"\b(?:ghp|gho|ghs|ghu|ghr)_[A-Za-z0-9]{30,}\b"),
    "github fine-grained token": re.compile(r"\bgithub_pat_[A-Za-z0-9_]{40,}\b"),
    "webhook secret assignment": re.compile(r"GITHUB_WEBHOOK_SECRET\s*=\s*\S{8,}"),
}
SECRET_NAMES = re.compile(r"(^|/)(\.env(\..+)?|.*\.pem)$")
ALLOWED_NAMES = {".env.example"}


def git(*args: str) -> bytes:
    return subprocess.run(["git", *args], capture_output=True, check=True).stdout


def main() -> int:
    problems: list[str] = []
    objects = git("rev-list", "--objects", "--all").decode().splitlines()
    for line in objects:
        sha, _, path = line.partition(" ")
        if not path:
            continue
        if SECRET_NAMES.search(path) and path.rsplit("/", 1)[-1] not in ALLOWED_NAMES:
            problems.append(f"secret-like file name in history: {path}")
        if git("cat-file", "-t", sha).strip() != b"blob":
            continue
        text = git("cat-file", "-p", sha).decode("utf-8", errors="ignore")
        for rule, pattern in PATTERNS.items():
            if pattern.search(text):
                problems.append(f"{rule}: {path or sha}")
    for p in problems:
        print("FOUND", p)
    print(f"scanned {len(objects)} objects; {len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())

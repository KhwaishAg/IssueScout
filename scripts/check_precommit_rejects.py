"""P0.1 'Done when' check: pre-commit must reject a badly formatted file.

Writes a deliberately ugly module, runs the hooks on it, and requires a non-zero exit.
The file is always removed afterwards.
"""

import subprocess
import sys
from pathlib import Path

BAD = Path("src/issuescout/_bad_format.py")


def main() -> int:
    BAD.write_text("import os,sys\ndef f( x ):\n  return x+1\n", encoding="utf-8")
    try:
        result = subprocess.run(
            ["uv", "run", "pre-commit", "run", "--files", str(BAD)],
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        BAD.unlink(missing_ok=True)
    print(result.stdout)
    if result.returncode == 0:
        print("FAIL: pre-commit accepted a badly formatted file")
        return 1
    print(f"OK: pre-commit rejected the bad file (exit {result.returncode})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

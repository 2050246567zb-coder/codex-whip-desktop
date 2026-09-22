from __future__ import annotations

import argparse
import subprocess
import sys


SHARED_PATHS = ("firmware/codex_whip", "firmware/tests")


def git(*arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("git", *arguments),
        check=check,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify that both product branches ship identical firmware."
    )
    parser.add_argument("other_ref", help="The other product branch, for example origin/codex/product-macos")
    args = parser.parse_args()

    if git("rev-parse", "--verify", args.other_ref, check=False).returncode:
        print(f"Firmware comparison ref does not exist: {args.other_ref}", file=sys.stderr)
        return 2

    result = git(
        "diff",
        "--exit-code",
        "--no-ext-diff",
        args.other_ref,
        "--",
        *SHARED_PATHS,
        check=False,
    )
    if result.returncode:
        print(
            "Shared firmware differs between product branches. "
            "Transfer the firmware commit to both branches before releasing.",
            file=sys.stderr,
        )
        print(result.stdout, file=sys.stderr)
        print(result.stderr, file=sys.stderr)
        return 1

    print(f"Shared firmware matches {args.other_ref}: {', '.join(SHARED_PATHS)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

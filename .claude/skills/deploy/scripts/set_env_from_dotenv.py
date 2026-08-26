#!/usr/bin/env python3
"""Push .env values to Railway as environment variables without exposing them.

`railway variable set KEY=VALUE` puts the secret directly in the command text --
visible in shell history and in an agent's own tool-call log. This reads values from
.env and pipes each one through `railway variable set KEY --stdin`, so the value never
appears in an argument list anywhere.

Usage: set_env_from_dotenv.py KEY1 KEY2 KEY3 ...
       set_env_from_dotenv.py --all          # push everything found in .env
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def load_dotenv(path: Path) -> dict[str, str]:
    out = {}
    if not path.exists():
        return out
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def main() -> int:
    env_path = Path(".env")
    values = load_dotenv(env_path)
    if not values:
        print(f"no values found in {env_path.resolve()}", file=sys.stderr)
        return 1

    args = sys.argv[1:]
    keys = list(values) if (not args or args == ["--all"]) else args

    ok = True
    for key in keys:
        val = values.get(key)
        if not val:
            print(f"{key} -> MISSING in .env, skipped")
            ok = False
            continue
        r = subprocess.run(
            ["npx", "--yes", "@railway/cli", "variable", "set", key,
             "--stdin", "--skip-deploys", "--json"],
            input=val, capture_output=True, text=True)
        status = "OK" if r.returncode == 0 else f"FAILED ({r.returncode})"
        print(f"{key} -> {status}")
        if r.returncode != 0:
            ok = False
            print(f"  stderr: {r.stderr[:200]}", file=sys.stderr)

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

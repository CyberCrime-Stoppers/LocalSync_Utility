#!/usr/bin/env python3
"""localsync_runner.py — CLI/cron executor for saved sync pairs.

Called by cron (through the managed crontab block the GUI installs) and
usable by hand:

    python3 localsync_runner.py --all
    python3 localsync_runner.py --pair "Documents"
    python3 localsync_runner.py --list

Prints one human-readable line per pair result; exits 0 when every pair
succeeded (so cron $? stays meaningful), 1 otherwise.
"""

import argparse
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import localsync_core as core


def run_pair(pair: dict, dry: bool = False) -> tuple[bool, str]:
    try:
        built = core.build_sync_command(pair, dry_run=dry)
    except core.BuildError as exc:
        return False, f"config error: {exc}"

    env = dict(os.environ)
    env.update(built["env"])
    argv = list(built["argv"])

    tmpfile = None
    if built["authfile"]:
        fd, tmpfile = tempfile.mkstemp(prefix="lsu-auth-")
        os.write(fd, built["authfile"].encode())
        os.close(fd)
        os.chmod(tmpfile, 0o600)
        argv = [tmpfile if a == "__AUTHFILE__" else a for a in argv]
    try:
        r = subprocess.run(argv, env=env, capture_output=True, text=True,
                           timeout=7200, check=False)
        ok = r.returncode == 0
        detail = (r.stderr or r.stdout or "ok").strip()[-300:]
        return ok, detail
    except subprocess.TimeoutExpired:
        return False, "timed out after 2h"
    finally:
        if tmpfile:
            try:
                os.unlink(tmpfile)
            except OSError:
                pass


def main() -> int:
    ap = argparse.ArgumentParser(description="LocalSync_Utility runner")
    ap.add_argument("--pair", help="run one pair by exact name")
    ap.add_argument("--all", action="store_true",
                    help="run every ENABLED pair")
    ap.add_argument("--list", action="store_true", help="list pairs")
    args = ap.parse_args()

    cfg = core.load_config()
    pairs = cfg.get("pairs", [])

    if args.list:
        for p in pairs:
            sched = core.schedule_expr(p) or "manual"
            state = "enabled" if p.get("enabled") else "disabled"
            print(f"{p['name']:30s} {p['protocol']:14s} {state:8s} {sched}")
        return 0

    if args.pair:
        targets = [p for p in pairs if p["name"] == args.pair]
        if not targets:
            print(f"no pair named '{args.pair}'")
            return 1
    elif args.all:
        targets = [p for p in pairs if p.get("enabled")]
    else:
        ap.print_help()
        return 1

    failures = 0
    for pair in targets:
        try:
            ok, detail = run_pair(pair)
        except Exception as exc:  # never die mid-loop on one bad pair
            ok, detail = False, str(exc)
        core.record_run(cfg, pair["name"], ok, detail)
        print(f"[{'OK' if ok else 'FAIL'}] {pair['name']}: {detail}")
        if not ok:
            failures += 1
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

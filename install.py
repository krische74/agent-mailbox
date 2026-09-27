#!/usr/bin/env python
"""Install or upgrade the agent mailbox kit in a repository.

    python install.py /path/to/repo [options]

Copies the kit into <repo>/.agents/mailbox and <repo>/.cursor, writes config.json, wires the
Cursor hooks, and adds the runtime files to .gitignore. Run it again with --upgrade to refresh
the scripts and docs in a repo that already has the kit; the existing config.json is kept unless
you pass options that change it.

Standard library only. Python 3.10 or newer.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys

KIT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kit")
SCRIPTS = ("mbx.py", "stop_hook.py", "guard_hook.py")
DOCS = ("GUARDRAILS.md", "PROTOCOL.md", "KICKOFF.md", "NUDGE.md")
HOOK_WAIT_SECONDS = 570
GITIGNORE_BLOCK = """
# agent mailbox runtime (the scripts, docs and config stay tracked)
.agents/mailbox/to_*.md
.agents/mailbox/state.json
.agents/mailbox/state.json.tmp
.agents/mailbox/*.tmp
.agents/mailbox/HALT
.agents/mailbox/log/
.agents/mailbox/__pycache__/
"""
MARKER = "# agent mailbox runtime"


def default_python(repo: str) -> str:
    if os.path.exists(os.path.join(repo, ".venv", "Scripts", "python.exe")):
        return ".venv\\Scripts\\python"
    if os.path.exists(os.path.join(repo, ".venv", "bin", "python")):
        return ".venv/bin/python"
    return "python" if os.name == "nt" else "python3"


def render(text: str, cfg: dict) -> str:
    return (
        text.replace("{{PLANNER}}", cfg["planner"])
        .replace("{{EXECUTOR}}", cfg["executor"])
        .replace("{{PY}}", cfg["python"])
    )


def loop_limit(cfg: dict) -> int:
    """Backstop for Cursor's own follow-up counter: enough for every cycle plus a re-arm per
    hook wait across the longest allowed session, and no more."""
    lim = cfg["limits"]
    rearms = math.ceil(int(lim["max_minutes"]) * 60 / HOOK_WAIT_SECONDS)
    return int(lim["max_cycles"]) + rearms + 5


def hook_entries(cfg: dict) -> dict:
    py = cfg["python"]
    guard = {
        "command": f"{py} .agents/mailbox/guard_hook.py",
        "timeout": 10,
        "failClosed": True,
    }
    return {
        "stop": {
            "command": f"{py} .agents/mailbox/stop_hook.py",
            "timeout": HOOK_WAIT_SECONDS + 30,
            "loop_limit": loop_limit(cfg),
        },
        "beforeShellExecution": dict(guard),
        "beforeReadFile": dict(guard),
    }


def merge_hooks(path: str, cfg: dict) -> None:
    data: dict = {"version": 1, "hooks": {}}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        data.setdefault("version", 1)
        data.setdefault("hooks", {})
    for event, entry in hook_entries(cfg).items():
        existing = [
            h
            for h in data["hooks"].get(event, [])
            if ".agents/mailbox/" not in h.get("command", "").replace("\\", "/")
        ]
        data["hooks"][event] = existing + [entry]
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")


def update_gitignore(repo: str) -> bool:
    path = os.path.join(repo, ".gitignore")
    current = ""
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            current = fh.read()
    if MARKER in current:
        return False
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        if current and not current.endswith("\n"):
            fh.write("\n")
        fh.write(GITIGNORE_BLOCK)
    return True


def build_config(existing: dict | None, args) -> dict:
    cfg = {
        "planner": "planner",
        "executor": "executor",
        "python": None,
        "gate": [],
        "limits": {"max_minutes": 240, "max_cycles": 30},
        "max_body_bytes": 200_000,
        "guard": {"enforce": "armed"},
    }
    if existing:
        cfg.update(existing)
        cfg["limits"] = dict(cfg["limits"], **(existing.get("limits") or {}))
    if args.planner:
        cfg["planner"] = args.planner
    if args.executor:
        cfg["executor"] = args.executor
    if args.python:
        cfg["python"] = args.python
    if args.gate is not None:
        cfg["gate"] = args.gate
    if args.max_minutes:
        cfg["limits"]["max_minutes"] = args.max_minutes
    if args.max_cycles:
        cfg["limits"]["max_cycles"] = args.max_cycles
    if args.enforce:
        cfg.setdefault("guard", {})["enforce"] = args.enforce
    if not cfg["python"]:
        cfg["python"] = default_python(args.repo)
    if cfg["planner"] == cfg["executor"]:
        raise SystemExit("planner and executor need different names")
    for name in (cfg["planner"], cfg["executor"]):
        if not name.replace("-", "").replace("_", "").isalnum():
            raise SystemExit(f"role name {name!r} may use letters, digits, - and _ only")
    return cfg


def self_test(mailbox: str, cfg: dict) -> list[str]:
    """Run the installed scripts with this interpreter to prove they load and behave."""
    problems = []
    status = subprocess.run(
        [sys.executable, os.path.join(mailbox, "mbx.py"), "status"],
        capture_output=True,
        text=True,
    )
    if status.returncode != 0:
        problems.append(f"mbx.py status failed: {status.stderr.strip()}")
    else:
        state = json.loads(status.stdout)
        if state.get("closed_reason") is None:
            problems.append("a session is armed right now; expected closed after install")
    guard = subprocess.run(
        [sys.executable, os.path.join(mailbox, "guard_hook.py")],
        input=json.dumps({"command": "git status"}),
        capture_output=True,
        text=True,
    )
    if guard.returncode != 0 or json.loads(guard.stdout or "{}").get("permission") != "allow":
        problems.append(f"guard_hook.py did not answer allow for 'git status': {guard.stdout} {guard.stderr}")
    return problems


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="install.py", description=__doc__.split("\n\n")[0])
    p.add_argument("repo", help="path to the repository to install into")
    p.add_argument("--planner", help="planner role name (default: planner)")
    p.add_argument("--executor", help="executor role name (default: executor)")
    p.add_argument("--python", help="interpreter command used in hooks and docs, e.g. .venv\\Scripts\\python")
    p.add_argument("--gate", nargs="*", help="commands the executor must pass before DONE, e.g. 'pytest -q'")
    p.add_argument("--max-minutes", type=int, help="ceiling for --minutes (default 240)")
    p.add_argument("--max-cycles", type=int, help="ceiling for --max-cycles (default 30)")
    p.add_argument("--enforce", choices=["armed", "always"], help="when the guard hook applies")
    p.add_argument("--upgrade", action="store_true", help="refresh an existing install")
    args = p.parse_args(argv)

    repo = os.path.abspath(args.repo)
    if not os.path.isdir(repo):
        print(f"not a directory: {repo}", file=sys.stderr)
        return 2
    mailbox = os.path.join(repo, ".agents", "mailbox")
    cfg_path = os.path.join(mailbox, "config.json")
    if os.path.exists(os.path.join(mailbox, "mbx.py")) and not args.upgrade:
        print("a mailbox is already installed here; rerun with --upgrade to refresh it", file=sys.stderr)
        return 2

    existing = None
    if os.path.exists(cfg_path):
        with open(cfg_path, encoding="utf-8") as fh:
            existing = json.load(fh)
    cfg = build_config(existing, args)

    os.makedirs(mailbox, exist_ok=True)
    for name in SCRIPTS:
        shutil.copyfile(os.path.join(KIT, "mailbox", name), os.path.join(mailbox, name))
    for name in DOCS:
        with open(os.path.join(KIT, "mailbox", name), encoding="utf-8") as fh:
            text = render(fh.read(), cfg)
        with open(os.path.join(mailbox, name), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
    with open(cfg_path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(cfg, fh, indent=2)
        fh.write("\n")

    cursor = os.path.join(repo, ".cursor")
    os.makedirs(os.path.join(cursor, "rules"), exist_ok=True)
    with open(os.path.join(KIT, "cursor", "rules", "mailbox.mdc"), encoding="utf-8") as fh:
        rule = render(fh.read(), cfg)
    with open(os.path.join(cursor, "rules", "mailbox.mdc"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(rule)
    merge_hooks(os.path.join(cursor, "hooks.json"), cfg)
    added_ignore = update_gitignore(repo)

    problems = self_test(mailbox, cfg)
    verb = "Upgraded" if args.upgrade else "Installed"
    print(f"{verb} the agent mailbox in {repo}")
    print(f"  roles: planner={cfg['planner']} executor={cfg['executor']}")
    print(f"  interpreter in hooks: {cfg['python']}")
    print(
        f"  limits: up to {cfg['limits']['max_minutes']} min, {cfg['limits']['max_cycles']} cycles per session"
    )
    print(f"  gate: {cfg['gate'] or '(none set; edit config.json)'}")
    print(f"  .gitignore {'updated' if added_ignore else 'already had the runtime entries'}")
    if problems:
        print("\nSELF-TEST FAILED:")
        for line in problems:
            print(f"  - {line}")
        return 1
    print("  self-test passed")
    print(
        "\nNext: restart Cursor so it loads .cursor/hooks.json, then check that the interpreter\n"
        f"command '{cfg['python']}' runs from the repo root. A hook whose command cannot start\n"
        "fails open for the stop hook and fails closed for the guard hook."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

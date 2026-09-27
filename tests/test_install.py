from __future__ import annotations

import json

from conftest import install


def test_renders_every_placeholder(repo):
    for path in list(repo.mailbox.glob("*.md")) + [repo.path / ".cursor" / "rules" / "mailbox.mdc"]:
        assert "{{" not in path.read_text(encoding="utf-8"), path


def test_writes_config(repo):
    cfg = json.loads((repo.mailbox / "config.json").read_text())
    assert cfg["planner"] == "planner" and cfg["executor"] == "executor"
    assert cfg["gate"] == ["pytest -q"]
    assert cfg["limits"] == {"max_minutes": 120, "max_cycles": 10}
    assert cfg["guard"]["enforce"] == "armed"


def test_hooks_json_has_guard_fail_closed_and_bounded_loop(repo):
    hooks = json.loads((repo.path / ".cursor" / "hooks.json").read_text())["hooks"]
    stop = hooks["stop"][0]
    assert stop["command"].endswith("stop_hook.py")
    # 10 cycles + ceil(120*60/570)=13 re-arms + 5
    assert stop["loop_limit"] == 28
    for event in ("beforeShellExecution", "beforeReadFile"):
        assert hooks[event][0]["failClosed"] is True
        assert hooks[event][0]["command"].endswith("guard_hook.py")


def test_existing_unrelated_hooks_are_kept(tmp_path):
    cursor = tmp_path / ".cursor"
    cursor.mkdir()
    mine = {"command": "node lint-on-edit.js"}
    (cursor / "hooks.json").write_text(
        json.dumps({"version": 1, "hooks": {"afterFileEdit": [mine], "stop": [mine]}})
    )
    assert install(tmp_path, "--python", "python").returncode == 0
    hooks = json.loads((cursor / "hooks.json").read_text())["hooks"]
    assert hooks["afterFileEdit"] == [mine]
    assert hooks["stop"][0] == mine and len(hooks["stop"]) == 2


def test_refuses_reinstall_without_upgrade(repo):
    r = install(repo.path)
    assert r.returncode == 2 and "--upgrade" in r.stderr


def test_upgrade_keeps_config_and_does_not_duplicate(repo):
    r = install(repo.path, "--upgrade")
    assert r.returncode == 0, r.stdout + r.stderr
    cfg = json.loads((repo.mailbox / "config.json").read_text())
    assert cfg["gate"] == ["pytest -q"] and cfg["limits"]["max_cycles"] == 10
    hooks = json.loads((repo.path / ".cursor" / "hooks.json").read_text())["hooks"]
    assert all(len(v) == 1 for v in hooks.values())
    assert (repo.path / ".gitignore").read_text().count("# agent mailbox runtime") == 1


def test_custom_role_names_flow_through(tmp_path):
    r = install(tmp_path, "--planner", "woebbe", "--executor", "cursor", "--python", ".venv\\Scripts\\python")
    assert r.returncode == 0, r.stdout + r.stderr
    kickoff = (tmp_path / ".agents" / "mailbox" / "KICKOFF.md").read_text()
    assert "--as cursor" in kickoff and "**woebbe**" in kickoff
    assert ".venv\\Scripts\\python .agents/mailbox/mbx.py" in kickoff


def test_rejects_same_role_names(tmp_path):
    r = install(tmp_path, "--planner", "bot", "--executor", "bot")
    assert r.returncode != 0

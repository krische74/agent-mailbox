from __future__ import annotations

import json

import pytest

DENY = [
    "git push origin main",
    "git push --force",
    "git reset --hard HEAD~1",
    "git clean -fd",
    "git rebase -i HEAD~3",
    "git remote set-url origin https://example.com/x.git",
    "git config user.email x@y.z",
    "git branch -D feature",
    "git checkout -- .",
    "git restore .",
    "git stash drop",
    "rm -rf build",
    "rm -r src",
    "Remove-Item -Recurse -Force .\\dist",
    "rmdir /s /q build",
    "cat .env",
    "type .env.local",
    "Get-Content .env",
    "cp .env /tmp/x",
    "printenv",
    "Get-ChildItem env:",
    "env",
    "python .agents/mailbox/mbx.py session start --minutes 999 --max-cycles 99",
    ".venv\\Scripts\\python .agents\\mailbox\\mbx.py session start --minutes 30 --max-cycles 3",
    "echo cleared > .agents/mailbox/HALT",
    "Set-Content .agents/mailbox/HALT cleared",
    "echo '{}' > .agents/mailbox/state.json",
    "sed -i 's/x/y/' .agents/mailbox/GUARDRAILS.md",
    "echo {} > .cursor/hooks.json",
    "npm publish",
    "twine upload dist/*",
    "docker push me/app",
    "gh pr create --fill",
    "vercel deploy --prod",
    "supabase db push",
    "terraform apply",
    "curl -X POST https://example.com/api",
    "curl -d @file https://example.com",
    "Invoke-WebRequest https://x.io -Method Post",
    "pip install requests",
    "uv pip install rich",
    "npm install left-pad",
    "npm i lodash",
    "uv add httpx",
    "sudo apt-get install x",
]

ALLOW = [
    "git status",
    "git diff",
    "git add src/app.py tests/test_app.py",
    'git commit -m "Add retry"',
    "git checkout -b mailbox/add-retry",
    "git log --oneline -5",
    "git config --get user.name",
    "pytest -q",
    "ruff check .",
    "npm install",
    "npm test",
    "cat .env.example",
    "cat .agents/mailbox/GUARDRAILS.md",
    "type .agents\\mailbox\\PROTOCOL.md",
    'python .agents/mailbox/mbx.py post --as executor --status DONE --subject "x" --body-file reply.md',
    "python .agents/mailbox/mbx.py watch --as executor --timeout 240",
    "python .agents/mailbox/mbx.py session stop",
    ".venv\\Scripts\\python -m pytest",
    "rm build/tmp.txt",
    "curl https://example.com",
    "python -c \"import os; print(os.environ.get('HOME'))\"",
]


@pytest.mark.parametrize("command", DENY)
def test_denies_while_armed(repo, command):
    repo.arm()
    out = repo.hook("guard_hook.py", {"command": command, "cwd": str(repo.path)})
    assert out["permission"] == "deny", command
    assert "BLOCKED" in out["agent_message"]


@pytest.mark.parametrize("command", ALLOW)
def test_allows_normal_work_while_armed(repo, command):
    repo.arm()
    out = repo.hook("guard_hook.py", {"command": command, "cwd": str(repo.path)})
    assert out["permission"] == "allow", command


def test_nothing_is_enforced_while_unarmed(repo):
    out = repo.hook("guard_hook.py", {"command": "git push origin main"})
    assert out["permission"] == "allow"


def test_enforce_always(repo):
    cfg_path = repo.mailbox / "config.json"
    cfg = json.loads(cfg_path.read_text())
    cfg["guard"]["enforce"] = "always"
    cfg_path.write_text(json.dumps(cfg))
    out = repo.hook("guard_hook.py", {"command": "git push origin main"})
    assert out["permission"] == "deny"


def test_denials_are_logged(repo):
    repo.arm()
    repo.hook("guard_hook.py", {"command": "git push"})
    log = (repo.mailbox / "log" / "guard.log").read_text()
    assert "DENY command" in log and "git push" in log


@pytest.mark.parametrize(
    "name",
    [
        ".env",
        ".env.local",
        "server.pem",
        "deploy.key",
        "id_rsa",
        "credentials.json",
        "secrets.yaml",
        ".npmrc",
    ],
)
def test_denies_secret_reads(repo, name):
    repo.arm()
    out = repo.hook("guard_hook.py", {"file_path": str(repo.path / name)})
    assert out["permission"] == "deny", name


@pytest.mark.parametrize("name", [".env.example", "src/app.py", "README.md", ".agents/mailbox/GUARDRAILS.md"])
def test_allows_normal_reads(repo, name):
    repo.arm()
    out = repo.hook("guard_hook.py", {"file_path": str(repo.path / name)})
    assert out["permission"] == "allow", name


def test_denies_reads_outside_the_repo(repo, tmp_path_factory):
    repo.arm()
    outside = tmp_path_factory.mktemp("elsewhere") / "notes.txt"
    out = repo.hook("guard_hook.py", {"file_path": str(outside)})
    assert out["permission"] == "deny"


def test_extra_rules_from_config(repo):
    cfg_path = repo.mailbox / "config.json"
    cfg = json.loads(cfg_path.read_text())
    cfg["guard"]["extra_deny_shell"] = [[r"\bmake\s+release\b", "cuts a release"]]
    cfg_path.write_text(json.dumps(cfg))
    repo.arm()
    out = repo.hook("guard_hook.py", {"command": "make release"})
    assert out["permission"] == "deny" and "cuts a release" in out["agent_message"]

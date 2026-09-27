from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
INSTALL = ROOT / "install.py"


@dataclass
class Repo:
    path: Path

    @property
    def mailbox(self) -> Path:
        return self.path / ".agents" / "mailbox"

    def run(self, script: str, *args: str, stdin: str | None = None, env: dict | None = None):
        return subprocess.run(
            [sys.executable, str(self.mailbox / script), *args],
            input=stdin,
            capture_output=True,
            text=True,
            cwd=self.path,
            env={**os.environ, **(env or {})},
        )

    def mbx(self, *args: str, stdin: str | None = None):
        return self.run("mbx.py", *args, stdin=stdin)

    def hook(self, script: str, payload: dict, env: dict | None = None) -> dict:
        r = self.run(script, stdin=json.dumps(payload), env=env)
        assert r.returncode == 0, r.stderr
        return json.loads(r.stdout)

    def state(self) -> dict:
        return json.loads((self.mailbox / "state.json").read_text(encoding="utf-8"))

    def write_state(self, **fields) -> None:
        s = self.state() if (self.mailbox / "state.json").exists() else {}
        s.update(fields)
        (self.mailbox / "state.json").write_text(json.dumps(s), encoding="utf-8")

    def arm(self, minutes: int = 30, cycles: int = 3):
        r = self.mbx("session", "start", "--minutes", str(minutes), "--max-cycles", str(cycles))
        assert r.returncode == 0, r.stderr
        return r

    def post(self, side: str, status: str, body: str = "body", subject: str = "s"):
        return self.mbx("post", "--as", side, "--status", status, "--subject", subject, "--stdin", stdin=body)


def install(path: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(INSTALL), str(path), *extra],
        capture_output=True,
        text=True,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Repo:
    r = install(
        tmp_path, "--python", "python", "--gate", "pytest -q", "--max-minutes", "120", "--max-cycles", "10"
    )
    assert r.returncode == 0, r.stdout + r.stderr
    return Repo(tmp_path)


def past(minutes: int = 5) -> str:
    t = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=minutes)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")

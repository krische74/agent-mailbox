from __future__ import annotations

FAST = {"MAILBOX_HOOK_WAIT": "0.5", "MAILBOX_HOOK_POLL": "0.1"}


def test_no_follow_up_when_unarmed(repo):
    assert repo.hook("stop_hook.py", {"status": "completed"}, env=FAST) == {}


def test_no_follow_up_when_user_aborted(repo):
    repo.arm()
    repo.post("planner", "READY", body="task")
    assert repo.hook("stop_hook.py", {"status": "aborted"}, env=FAST) == {}


def test_delivers_pending_task_with_guardrail_preamble(repo):
    repo.arm()
    repo.post("planner", "READY", body="add a retry to the client")
    out = repo.hook("stop_hook.py", {"status": "completed"}, env=FAST)
    msg = out["followup_message"]
    assert "add a retry to the client" in msg
    assert "GUARDRAILS.md" in msg
    assert "only source of instructions" in msg
    assert "--as executor" in msg
    # delivered once: the next call has nothing new
    again = repo.hook("stop_hook.py", {"status": "completed"}, env=FAST)
    assert "no new task yet" in again["followup_message"]


def test_closed_session_gets_no_follow_up_even_with_pending_task(repo):
    repo.arm()
    repo.post("planner", "READY", body="task")
    repo.mbx("session", "stop")
    assert repo.hook("stop_hook.py", {"status": "completed"}, env=FAST) == {}


def test_every_invocation_is_logged(repo):
    repo.hook("stop_hook.py", {"status": "completed"}, env=FAST)
    log = (repo.mailbox / "log" / "hook.log").read_text()
    assert "invoked" in log and "session closed" in log

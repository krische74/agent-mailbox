from __future__ import annotations

import json

from conftest import past


def closed_reason(repo) -> str | None:
    r = repo.mbx("status")
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)["closed_reason"]


# ------------------------------------------------------------------ arming limits
def test_fresh_install_is_closed(repo):
    assert closed_reason(repo) == "session not started (halted)"


def test_session_start_requires_both_limits(repo):
    for args in (["--minutes", "30"], ["--max-cycles", "3"], []):
        r = repo.mbx("session", "start", *args)
        assert r.returncode == 2
        assert "SESSION_REFUSED" in r.stderr
    assert closed_reason(repo) is not None


def test_session_start_rejects_zero_and_over_ceiling(repo):
    for minutes, cycles in (("0", "3"), ("30", "0"), ("121", "3"), ("30", "11"), ("-5", "3")):
        r = repo.mbx("session", "start", "--minutes", minutes, "--max-cycles", cycles)
        assert r.returncode == 2, (minutes, cycles)
    assert closed_reason(repo) is not None


def test_session_start_within_limits_opens(repo):
    repo.arm(minutes=120, cycles=10)
    assert closed_reason(repo) is None


def test_session_stop_closes(repo):
    repo.arm()
    assert repo.mbx("session", "stop").returncode == 0
    assert closed_reason(repo) is not None


# ------------------------------------------------------------------ fail closed
def test_running_state_without_cycle_cap_is_closed(repo):
    repo.arm()
    repo.write_state(max_cycles=0)
    assert "no cycle cap" in closed_reason(repo)


def test_running_state_without_deadline_is_closed(repo):
    repo.arm()
    repo.write_state(deadline_utc=None)
    assert closed_reason(repo) == "no deadline set"


def test_unreadable_deadline_is_closed(repo):
    repo.arm()
    repo.write_state(deadline_utc="next tuesday")
    assert "deadline unreadable" in closed_reason(repo)


def test_expired_deadline_is_closed(repo):
    repo.arm()
    repo.write_state(deadline_utc=past())
    assert "time budget expired" in closed_reason(repo)


def test_corrupt_state_file_is_closed(repo):
    repo.arm()
    (repo.mailbox / "state.json").write_text("{not json", encoding="utf-8")
    assert closed_reason(repo) is not None


def test_halt_overwrite_stops_and_touch_does_not(repo):
    repo.arm()
    halt = repo.mailbox / "HALT"
    halt.touch()  # updates the timestamp only; first line still starts with "cleared"
    assert closed_reason(repo) is None
    halt.write_text("halted by hand\n", encoding="utf-8")
    assert closed_reason(repo) == "HALT file present"


# ------------------------------------------------------------------ roles and posting
def test_executor_cannot_post_ready_and_planner_cannot_post_done(repo):
    repo.arm()
    r = repo.post("executor", "READY")
    assert r.returncode == 2 and "POST_REFUSED" in r.stderr
    r = repo.post("planner", "DONE")
    assert r.returncode == 2 and "POST_REFUSED" in r.stderr


def test_ready_into_closed_session_is_refused(repo):
    r = repo.post("planner", "READY")
    assert r.returncode == 3
    assert not (repo.mailbox / "to_executor.md").exists()


def test_done_into_closed_session_still_posts(repo):
    r = repo.post("executor", "DONE", body="finished after the deadline")
    assert r.returncode == 0
    assert "WARNING" in r.stdout
    assert (repo.mailbox / "to_planner.md").exists()


def test_body_size_cap(repo):
    repo.arm()
    r = repo.post("planner", "READY", body="x" * 200_001)
    assert r.returncode == 2 and "cap" in r.stderr


def test_round_trip_counts_cycles_and_closes_at_cap(repo):
    repo.arm(cycles=2)
    for n in (1, 2):
        assert repo.post("planner", "READY", body=f"task {n}").returncode == 0
        w = repo.mbx("watch", "--as", "executor", "--timeout", "1", "--poll", "0.1")
        assert w.returncode == 0 and f"task {n}" in w.stdout
        assert repo.post("executor", "DONE", body=f"done {n}").returncode == 0
        assert repo.state()["cycle"] == n
    assert "cycle cap reached" in closed_reason(repo)
    # the planner still collects the final reply, then sees the closure
    w = repo.mbx("watch", "--as", "planner", "--timeout", "1", "--poll", "0.1")
    assert w.returncode == 0 and "done 2" in w.stdout
    w = repo.mbx("watch", "--as", "planner", "--timeout", "1", "--poll", "0.1")
    assert w.returncode == 3


def test_closed_session_never_delivers_pending_work_to_executor(repo):
    repo.arm()
    assert repo.post("planner", "READY", body="do this").returncode == 0
    repo.mbx("session", "stop")
    w = repo.mbx("watch", "--as", "executor", "--timeout", "1", "--poll", "0.1")
    assert w.returncode == 3
    assert "do this" not in w.stdout


def test_watch_times_out_with_exit_4(repo):
    repo.arm()
    w = repo.mbx("watch", "--as", "executor", "--timeout", "0.3", "--poll", "0.1")
    assert w.returncode == 4


def test_every_post_is_logged_with_increasing_seq(repo):
    repo.arm()
    repo.post("planner", "READY")
    repo.post("executor", "ACK")
    repo.post("executor", "DONE")
    names = sorted(p.name for p in (repo.mailbox / "log").glob("0*.md"))
    assert [n[:4] for n in names] == ["0001", "0002", "0003"]


def test_seq_self_heals_from_log_when_state_is_lost(repo):
    repo.arm()
    repo.post("planner", "READY")
    repo.post("executor", "DONE")
    (repo.mailbox / "state.json").unlink()
    repo.arm()
    repo.post("planner", "READY")
    assert repo.state()["seq"] == 3

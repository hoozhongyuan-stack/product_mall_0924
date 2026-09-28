"""Local-only Phase C maintenance scheduler. No third-party credentials are inherited.

Run directly with the project venv. This deliberately does not install a system
schedule or provide a production scheduler configuration.
"""
import argparse
import fcntl
import json
import os
import re
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
DEFAULT_STATE_DIR = BACKEND_DIR.parent / "secrets" / "phase_c_local_scheduler"
LOCK_ROOT = BACKEND_DIR.parent / "secrets" / "phase_c_local_locks"
LOCAL_DB = re.compile(r"c4\d+_(?:local|cross_client)_[a-z0-9_]+\Z")
SUMMARY = re.compile(
    r"^closed=(?:\d+|FAILED) auto_confirmed=(?:\d+|FAILED) "
    r"wechat=(?:SKIPPED_UNCONFIGURED|RUN|FAILED)$", re.MULTILINE)


def utc_now():
    return datetime.now(timezone.utc)


def validate_target(env, expected_db):
    """Require an explicit disposable local database before starting Django."""
    return (bool(LOCAL_DB.fullmatch(expected_db))
            and env.get("POSTGRES_DB") == expected_db
            and env.get("POSTGRES_HOST", "127.0.0.1") in {"127.0.0.1", "localhost", "::1"})


def child_environment(env):
    child = {key: value for key, value in env.items()
             if not key.startswith(("WECHAT_PAY_", "WECHAT_MINI_APP_", "KDNIAO_", "MALL_WECHAT_"))}
    child["DJANGO_SETTINGS_MODULE"] = "config.settings"
    child["KDNIAO_ENABLED"] = "0"
    return child


def write_json(path, value):
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, sort_keys=True)
        handle.write("\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def log_event(state_dir, event):
    data = json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n"
    fd = os.open(state_dir / "events.jsonl", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, data.encode("utf-8"))
    finally:
        os.close(fd)
    print(data, end="", flush=True)


def alert_path(db):
    return LOCK_ROOT / f"{db}.alert.json"


def checkpoint_path(db):
    return LOCK_ROOT / f"{db}.last_tick.json"


@contextmanager
def alert_mutex(db):
    with (LOCK_ROOT / f"{db}.alert.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def alert_contents(db):
    try:
        return alert_path(db).read_bytes()
    except FileNotFoundError:
        return None


def set_alert(db, event):
    with alert_mutex(db):
        write_json(alert_path(db), event)


def clear_alert_if_unchanged(db, prior_contents):
    with alert_mutex(db):
        if alert_contents(db) == prior_contents:
            alert_path(db).unlink(missing_ok=True)


def recovery_gap(state_dir, db, *, interval_seconds, now):
    state_path = checkpoint_path(db)
    if not state_path.exists():
        state_path = state_dir / "last_tick.json"  # Legacy local rehearsal checkpoint.
    if not state_path.exists():
        return False
    try:
        checkpoint = json.loads(state_path.read_text(encoding="utf-8"))
        previous = datetime.fromisoformat(checkpoint["started_at"])
    except (OSError, ValueError, KeyError, TypeError):
        event = {"at": now.isoformat(), "event": "INVALID_CHECKPOINT"}
        log_event(state_dir, event)
        set_alert(db, event)
        return True
    if previous.tzinfo is None or "finished_at" not in checkpoint:
        event = {"at": now.isoformat(), "event": "INTERRUPTED_TICK"}
        log_event(state_dir, event)
        set_alert(db, event)
        return True
    elapsed = (now - previous).total_seconds()
    if elapsed <= interval_seconds * 1.5:
        return False
    event = {"at": now.isoformat(), "event": "MISSED_INTERVALS",
             "elapsed_seconds": round(elapsed), "estimated_missed": max(1, int(elapsed // interval_seconds) - 1)}
    log_event(state_dir, event)
    set_alert(db, event)
    return True


def run_tick(state_dir, env, *, limit, timeout_seconds, lock_fd, preserve_alert=False):
    started_at = utc_now()
    db = env["POSTGRES_DB"]
    prior_alert = alert_contents(db)
    state_path = checkpoint_path(db)
    write_json(state_path, {"started_at": started_at.isoformat()})
    command = [sys.executable, str(BACKEND_DIR / "manage.py"),
               "run_phase_c_jobs", "--limit", str(limit)]
    event = {"at": started_at.isoformat(), "db": env["POSTGRES_DB"], "event": "TICK_OK",
             "exit_code": 0, "summary": ""}
    try:
        result = subprocess.run(command, env=child_environment(env), cwd=BACKEND_DIR.parent,
                                capture_output=True, text=True, timeout=timeout_seconds, check=False,
                                pass_fds=(lock_fd,))
        event["exit_code"] = result.returncode
        summaries = list(SUMMARY.finditer(result.stdout))
        event["summary"] = summaries[-1].group(0) if summaries else ""
        if result.returncode:
            event["event"] = "TICK_FAILED"
        elif len(summaries) != 1 or "FAILED" in event["summary"]:
            event.update(event="TICK_INVALID_RESULT", exit_code=70)
        elif "wechat=SKIPPED_UNCONFIGURED" not in event["summary"]:
            event.update(event="TICK_UNSAFE_PROVIDER_RESULT", exit_code=70)
    except subprocess.TimeoutExpired:
        event.update(event="TICK_TIMEOUT", exit_code=124)
    except OSError:
        event.update(event="TICK_EXEC_ERROR", exit_code=70)
    event["duration_ms"] = round((utc_now() - started_at).total_seconds() * 1000)
    log_event(state_dir, event)
    if event["exit_code"]:
        set_alert(db, event)
    elif not preserve_alert:
        clear_alert_if_unchanged(db, prior_alert)
    write_json(state_path, {"started_at": started_at.isoformat(),
                            "finished_at": utc_now().isoformat(), "exit_code": event["exit_code"]})
    return event["exit_code"]


def main(argv=None, *, env=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="Run one tick (default).")
    mode.add_argument("--loop", action="store_true", help="Run a local recurring tick.")
    parser.add_argument("--db", required=True, help="Expected disposable c4*_local_* or c4*_cross_client_* PostgreSQL database.")
    parser.add_argument("--state-dir", type=Path, default=DEFAULT_STATE_DIR)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--timeout-seconds", type=int, default=45)
    parser.add_argument("--interval-seconds", type=int, default=60)
    parser.add_argument("--max-ticks", type=int, default=0, help="Stop a loop after N ticks; 0 means ongoing.")
    args = parser.parse_args(argv)
    env = dict(os.environ if env is None else env)
    if not validate_target(env, args.db):
        print("C4 local scheduler requires an explicit matching disposable local PostgreSQL database.", file=sys.stderr)
        return 64
    if not 1 <= args.limit <= 500 or not 1 <= args.timeout_seconds <= 55 or not 1 <= args.interval_seconds <= 3600 or args.max_ticks < 0:
        print("Invalid scheduler bounds.", file=sys.stderr)
        return 64
    os.umask(0o077)
    args.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    LOCK_ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (LOCK_ROOT / f"{args.db}.lock").open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            event = {"at": utc_now().isoformat(), "event": "SKIPPED_OVERLAP", "exit_code": 75}
            log_event(args.state_dir, event)
            set_alert(args.db, event)
            return 75
        ticks = 0
        try:
            while True:
                next_start = time.monotonic() + args.interval_seconds
                missed = recovery_gap(args.state_dir, args.db, interval_seconds=args.interval_seconds, now=utc_now())
                code = run_tick(args.state_dir, env, limit=args.limit, timeout_seconds=args.timeout_seconds,
                                lock_fd=lock.fileno(), preserve_alert=missed)
                ticks += 1
                if not args.loop or (args.max_ticks and ticks >= args.max_ticks):
                    return code
                time.sleep(max(0, next_start - time.monotonic()))
        except KeyboardInterrupt:
            return 0


if __name__ == "__main__":
    raise SystemExit(main())

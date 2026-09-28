"""Local scheduler boundaries; no PostgreSQL or provider connection is needed."""
import fcntl
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.test import SimpleTestCase

from config import phase_c_local_scheduler as scheduler


class LocalSchedulerTests(SimpleTestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state_dir = Path(self.tmp.name)
        self.lock_root = self.state_dir / "locks"
        self.alert_path = self.lock_root / "c43_local_scheduler_test.alert.json"
        self.checkpoint_path = self.lock_root / "c43_local_scheduler_test.last_tick.json"
        lock_patch = patch.object(scheduler, "LOCK_ROOT", self.lock_root)
        lock_patch.start()
        self.addCleanup(lock_patch.stop)
        self.env = {
            "POSTGRES_DB": "c43_local_scheduler_test",
            "POSTGRES_HOST": "127.0.0.1",
            "POSTGRES_PASSWORD": "synthetic-secret",
            "WECHAT_PAY_MERCHANT_ID": "must-not-leak",
            "WECHAT_PAY_PLATFORM_KEYS": "not-json",
            "MALL_WECHAT_CREDENTIAL_KEY_FILE": "private-path-must-not-leak",
            "KDNIAO_ENABLED": "1",
            "KDNIAO_APP_KEY": "must-not-leak",
        }

    def args(self, *extra):
        return ["--once", "--db", self.env["POSTGRES_DB"], "--state-dir", str(self.state_dir), *extra]

    def test_rejects_nonisolated_or_remote_database_before_job(self):
        with patch.object(scheduler.subprocess, "run") as runner:
            self.assertEqual(scheduler.main(self.args(), env={**self.env, "POSTGRES_DB": "product_mall_dev"}), 64)
            self.assertEqual(scheduler.main(self.args(), env={**self.env, "POSTGRES_HOST": "db.example"}), 64)
            self.assertEqual(scheduler.main(self.args(), env={**self.env, "POSTGRES_DB": "c43_local_wrong"}), 64)
        runner.assert_not_called()

    def test_once_scrubs_provider_config_and_writes_safe_success_log(self):
        completed = subprocess.CompletedProcess([], 0, "closed=1 auto_confirmed=0 wechat=SKIPPED_UNCONFIGURED\n", "")
        with patch.object(scheduler.subprocess, "run", return_value=completed) as runner:
            self.assertEqual(scheduler.main(self.args(), env=self.env), 0)
        child_env = runner.call_args.kwargs["env"]
        self.assertEqual(child_env["POSTGRES_DB"], self.env["POSTGRES_DB"])
        self.assertNotIn("WECHAT_PAY_MERCHANT_ID", child_env)
        self.assertNotIn("WECHAT_PAY_PLATFORM_KEYS", child_env)
        self.assertNotIn("MALL_WECHAT_CREDENTIAL_KEY_FILE", child_env)
        self.assertNotIn("KDNIAO_APP_KEY", child_env)
        self.assertEqual(child_env["KDNIAO_ENABLED"], "0")
        self.assertFalse(self.alert_path.exists())
        self.assertTrue(runner.call_args.kwargs["pass_fds"])
        log = json.loads((self.state_dir / "events.jsonl").read_text().splitlines()[-1])
        self.assertEqual(log["event"], "TICK_OK")
        self.assertEqual(log["summary"], "closed=1 auto_confirmed=0 wechat=SKIPPED_UNCONFIGURED")
        self.assertNotIn("synthetic-secret", json.dumps(log))

    def test_failed_tick_sets_alert_and_next_success_clears_it(self):
        results = [subprocess.CompletedProcess([], 1, "closed=FAILED auto_confirmed=0", "secret detail"),
                   subprocess.CompletedProcess([], 0, "closed=0 auto_confirmed=0 wechat=SKIPPED_UNCONFIGURED", "")]
        with patch.object(scheduler.subprocess, "run", side_effect=results):
            self.assertEqual(scheduler.main(self.args(), env=self.env), 1)
            self.assertTrue(self.alert_path.exists())
            self.assertEqual(scheduler.main(self.args(), env=self.env), 0)
        self.assertFalse(self.alert_path.exists())
        events = [json.loads(line) for line in (self.state_dir / "events.jsonl").read_text().splitlines()]
        self.assertEqual([event["event"] for event in events], ["TICK_FAILED", "TICK_OK"])
        self.assertNotIn("secret detail", (self.state_dir / "events.jsonl").read_text())

    def test_timeout_is_bounded_and_alerted(self):
        with patch.object(scheduler.subprocess, "run", side_effect=subprocess.TimeoutExpired("job", 2)) as runner:
            self.assertEqual(scheduler.main(self.args("--timeout-seconds", "2"), env=self.env), 124)
        self.assertEqual(runner.call_args.kwargs["timeout"], 2)
        self.assertEqual(json.loads(self.alert_path.read_text())["event"], "TICK_TIMEOUT")

    def test_unexpected_provider_result_is_not_marked_healthy(self):
        completed = subprocess.CompletedProcess([], 0, "closed=0 auto_confirmed=0 wechat=RUN", "")
        with patch.object(scheduler.subprocess, "run", return_value=completed):
            self.assertEqual(scheduler.main(self.args(), env=self.env), 70)
        self.assertEqual(json.loads(self.alert_path.read_text())["event"],
                         "TICK_UNSAFE_PROVIDER_RESULT")

    def test_zero_exit_with_failed_subjob_is_not_marked_healthy(self):
        completed = subprocess.CompletedProcess([], 0,
                                                "closed=FAILED auto_confirmed=0 wechat=SKIPPED_UNCONFIGURED", "")
        with patch.object(scheduler.subprocess, "run", return_value=completed):
            self.assertEqual(scheduler.main(self.args(), env=self.env), 70)
        self.assertEqual(json.loads(self.alert_path.read_text())["event"],
                         "TICK_INVALID_RESULT")

    def test_parallel_invocation_skips_under_process_lock(self):
        self.lock_root.mkdir()
        lock_path = self.lock_root / f"{self.env['POSTGRES_DB']}.lock"
        with lock_path.open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with patch.object(scheduler.subprocess, "run") as runner:
                self.assertEqual(scheduler.main(self.args(), env=self.env), 75)
            runner.assert_not_called()

    def test_same_database_with_different_state_dirs_still_skips_overlap(self):
        fixed_locks = self.lock_root
        fixed_locks.mkdir()
        alternate = self.state_dir / "alternate"
        lock_path = fixed_locks / f"{self.env['POSTGRES_DB']}.lock"
        completed = subprocess.CompletedProcess([], 0,
                                                "closed=0 auto_confirmed=0 wechat=SKIPPED_UNCONFIGURED", "")
        with lock_path.open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with patch.object(scheduler.subprocess, "run", return_value=completed) as runner:
                self.assertEqual(scheduler.main(self.args("--state-dir", str(alternate)), env=self.env), 75)
            runner.assert_not_called()
        self.assertEqual(json.loads(self.alert_path.read_text())["event"], "SKIPPED_OVERLAP")
        with patch.object(scheduler.subprocess, "run", return_value=completed):
            self.assertEqual(scheduler.main(self.args(), env=self.env), 0)
        self.assertFalse(self.alert_path.exists())

    def test_restart_after_missed_minute_records_gap_then_runs_immediately(self):
        old = datetime.now(timezone.utc) - timedelta(minutes=4)
        self.lock_root.mkdir()
        self.checkpoint_path.write_text(json.dumps({"started_at": old.isoformat(),
                                                    "finished_at": old.isoformat()}))
        completed = subprocess.CompletedProcess([], 0, "closed=0 auto_confirmed=0 wechat=SKIPPED_UNCONFIGURED", "")
        with patch.object(scheduler.subprocess, "run", return_value=completed):
            self.assertEqual(scheduler.main(["--loop", "--max-ticks", "1", "--db", self.env["POSTGRES_DB"],
                                            "--state-dir", str(self.state_dir)], env=self.env), 0)
        events = [json.loads(line)["event"] for line in (self.state_dir / "events.jsonl").read_text().splitlines()]
        self.assertEqual(events, ["MISSED_INTERVALS", "TICK_OK"])
        self.assertTrue(self.alert_path.exists())

    def test_recent_unfinished_tick_alerts_and_retries_immediately(self):
        recent = datetime.now(timezone.utc)
        self.lock_root.mkdir()
        self.checkpoint_path.write_text(json.dumps({"started_at": recent.isoformat()}))
        completed = subprocess.CompletedProcess([], 0, "closed=0 auto_confirmed=0 wechat=SKIPPED_UNCONFIGURED", "")
        with patch.object(scheduler.subprocess, "run", return_value=completed):
            self.assertEqual(scheduler.main(["--loop", "--max-ticks", "1", "--db", self.env["POSTGRES_DB"],
                                            "--state-dir", str(self.state_dir)], env=self.env), 0)
        events = [json.loads(line)["event"] for line in (self.state_dir / "events.jsonl").read_text().splitlines()]
        self.assertEqual(events, ["INTERRUPTED_TICK", "TICK_OK"])
        self.assertTrue(self.alert_path.exists())
        self.assertIn("finished_at", json.loads(self.checkpoint_path.read_text()))

    def test_new_state_dir_recovers_shared_unfinished_checkpoint(self):
        alternate = self.state_dir / "alternate"
        self.lock_root.mkdir()
        self.checkpoint_path.write_text(json.dumps({"started_at": datetime.now(timezone.utc).isoformat()}))
        completed = subprocess.CompletedProcess([], 0, "closed=0 auto_confirmed=0 wechat=SKIPPED_UNCONFIGURED", "")
        with patch.object(scheduler.subprocess, "run", return_value=completed):
            self.assertEqual(scheduler.main(self.args("--state-dir", str(alternate)), env=self.env), 0)
        events = [json.loads(line)["event"] for line in (alternate / "events.jsonl").read_text().splitlines()]
        self.assertEqual(events, ["INTERRUPTED_TICK", "TICK_OK"])

    def test_overlap_alert_created_during_successful_tick_remains_active(self):
        completed = subprocess.CompletedProcess([], 0, "closed=0 auto_confirmed=0 wechat=SKIPPED_UNCONFIGURED", "")

        def overlapping_run(*args, **kwargs):
            scheduler.set_alert(self.env["POSTGRES_DB"], {"at": datetime.now(timezone.utc).isoformat(),
                                                           "event": "SKIPPED_OVERLAP", "exit_code": 75})
            return completed

        with patch.object(scheduler.subprocess, "run", side_effect=overlapping_run):
            self.assertEqual(scheduler.main(self.args(), env=self.env), 0)
        self.assertEqual(json.loads(self.alert_path.read_text())["event"], "SKIPPED_OVERLAP")
        with patch.object(scheduler.subprocess, "run", return_value=completed):
            self.assertEqual(scheduler.main(self.args(), env=self.env), 0)
        self.assertFalse(self.alert_path.exists())

    def test_loop_retries_failed_tick_at_next_interval(self):
        results = [subprocess.CompletedProcess([], 1, "closed=FAILED", ""),
                   subprocess.CompletedProcess([], 0, "closed=0 auto_confirmed=0 wechat=SKIPPED_UNCONFIGURED", "")]
        with patch.object(scheduler.subprocess, "run", side_effect=results) as runner, \
             patch.object(scheduler.time, "sleep") as sleep:
            self.assertEqual(scheduler.main(["--loop", "--max-ticks", "2", "--interval-seconds", "1",
                                             "--db", self.env["POSTGRES_DB"],
                                             "--state-dir", str(self.state_dir)], env=self.env), 0)
        self.assertEqual(runner.call_count, 2)
        sleep.assert_called_once()
        self.assertFalse(self.alert_path.exists())

    def test_child_keeps_database_lock_if_parent_file_descriptor_closes(self):
        self.lock_root.mkdir()
        lock_path = self.lock_root / f"{self.env['POSTGRES_DB']}.lock"
        with lock_path.open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(1)"],
                                     pass_fds=(lock.fileno(),))
        try:
            with lock_path.open("w") as second:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(second, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            child.terminate()
            child.wait(timeout=3)

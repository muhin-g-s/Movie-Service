"""Тесты tools/capture-reminder.py: напоминания о записи знаний из чата."""
import json
import re
import unittest

from helpers import Repo


class ReminderCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo = Repo()
        encoded = re.sub(r"[^A-Za-z0-9]", "-", str(cls.repo.dir))
        cls.state = cls.repo.home / ".claude" / "projects" / encoded / "capture"

    @classmethod
    def tearDownClass(cls):
        cls.repo.close()

    def setUp(self):
        import shutil
        shutil.rmtree(self.state, ignore_errors=True)

    def call(self, command, payload=None, extra_env=None):
        stdin = json.dumps(payload) if payload is not None else ""
        return self.repo.run(["python3", "tools/capture-reminder.py", command], stdin=stdin, extra_env=extra_env)

    def stops(self, count, session="s1", **payload):
        return [self.call("stop", {"session_id": session, **payload}).returncode for _ in range(count)]


class TestStop(ReminderCase):
    def test_reminds_only_on_every_twelfth_turn(self):
        self.assertEqual(self.stops(11), [0] * 11)
        result = self.call("stop", {"session_id": "s1"})
        self.assertEqual(result.returncode, 2)
        self.assertIn("/capture", result.stderr)

    def test_reminds_again_on_the_twenty_fourth_turn(self):
        self.stops(12)
        self.assertEqual(self.stops(11), [0] * 11)
        self.assertEqual(self.call("stop", {"session_id": "s1"}).returncode, 2)

    def test_stop_hook_active_never_blocks_and_is_not_counted(self):
        self.stops(11)
        self.assertEqual(self.call("stop", {"session_id": "s1", "stop_hook_active": True}).returncode, 0)
        self.assertEqual(self.call("stop", {"session_id": "s1"}).returncode, 2)

    def test_frequency_is_configurable_and_zero_disables(self):
        self.assertEqual(self.call("stop", {"session_id": "a"}, {"CAPTURE_REMIND_EVERY": "2"}).returncode, 0)
        self.assertEqual(self.call("stop", {"session_id": "a"}, {"CAPTURE_REMIND_EVERY": "2"}).returncode, 2)
        for _ in range(15):
            self.assertEqual(self.call("stop", {"session_id": "b"}, {"CAPTURE_REMIND_EVERY": "0"}).returncode, 0)

    def test_sessions_are_counted_separately(self):
        self.stops(11, session="one")
        self.assertEqual(self.call("stop", {"session_id": "two"}).returncode, 0)

    def test_real_stop_payload_shape(self):
        from helpers import hook_payload
        result = self.repo.run(["python3", "tools/capture-reminder.py", "stop"], stdin=hook_payload("Stop", self.repo.dir))
        self.assertEqual(result.returncode, 0)


class TestPendingMarks(ReminderCase):
    def pending(self):
        path = self.state / "pending"
        return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()] if path.exists() else []

    def test_short_session_is_not_marked_on_end(self):
        self.stops(5)
        self.call("end", {"session_id": "s1", "reason": "other"})
        self.assertEqual(self.pending(), [])

    def test_long_session_is_marked_on_end(self):
        self.stops(6)
        self.call("end", {"session_id": "s1", "reason": "clear"})
        marks = self.pending()
        self.assertEqual([m["reason"] for m in marks], ["clear"])

    def test_compaction_always_marks(self):
        self.call("compact", {"session_id": "s9"})
        self.assertEqual([m["reason"] for m in self.pending()], ["compact"])

    def test_start_reminds_with_count_and_keeps_marks(self):
        self.call("compact", {"session_id": "a"})
        self.call("compact", {"session_id": "b"})
        out = self.call("start", {}).stdout
        self.assertIn("2 прошлых", out)
        self.assertIn("/harvest", out)
        self.assertEqual(len(self.pending()), 2)

    def test_start_is_silent_without_marks(self):
        self.assertEqual(self.call("start", {}).stdout, "")

    def test_clear_removes_marks(self):
        self.call("compact", {"session_id": "a"})
        self.assertEqual(self.call("clear").returncode, 0)
        self.assertEqual(self.pending(), [])
        self.assertEqual(self.call("start", {}).stdout, "")


class TestRobustness(ReminderCase):
    def test_bad_input_never_crashes(self):
        for command in ("stop", "end", "compact", "start"):
            for stdin in ("not json", "[]", "null", ""):
                with self.subTest(command=command, stdin=stdin):
                    result = self.repo.run(["python3", "tools/capture-reminder.py", command], stdin=stdin)
                    self.assertEqual(result.returncode, 0, result.stderr)

    def test_unknown_command_fails(self):
        self.assertEqual(self.call("bogus").returncode, 1)

    def test_session_id_is_sanitised_for_file_names(self):
        self.call("stop", {"session_id": "../../etc/passwd"})
        names = [p.name for p in self.state.glob("*")]
        self.assertTrue(all("/" not in n for n in names))
        self.assertFalse((self.repo.home / "etc").exists())


if __name__ == "__main__":
    unittest.main()

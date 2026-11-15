"""Тесты tools/guard-constitution.py: запрос подтверждения при записи в конституцию."""
import json
import subprocess
import sys
import unittest

from helpers import ROOT, hook_payload

SCRIPT = ROOT / "tools" / "guard-constitution.py"
FILE = ".specify/memory/constitution.md"


def run_guard(stdin):
    return subprocess.run([sys.executable, str(SCRIPT)], input=stdin, capture_output=True, text=True)


def payload_for(command):
    payload = json.loads(hook_payload("PreToolUse", ROOT))
    payload["tool_input"]["command"] = command
    return json.dumps(payload)


class TestGuard(unittest.TestCase):
    def test_reading_is_allowed(self):
        for command in (
            f"cat {FILE}",
            f"grep -n IX {FILE} | head",
            f"git diff -- {FILE}",
            f"git log --oneline -- {FILE}",
            f"cat {FILE} 2>&1 | head",
            f"sed -n 1,5p {FILE} >/dev/null",
            "ls docs",
        ):
            with self.subTest(command=command):
                result = run_guard(payload_for(command))
                self.assertEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")

    def test_writing_asks_for_confirmation(self):
        for command in (
            f"sed -i '' s/a/b/ {FILE}",
            f"echo x >> {FILE}",
            f"python3 - <<'E'\nopen('{FILE}','w')\nE",
            f"cp /tmp/x {FILE}",
            f"git checkout -- {FILE}",
            f"vim {FILE}",
            f"truncate -s0 {FILE}",
            f"dd of={FILE}",
            f"cat x &>{FILE}",
            f"curl -o {FILE} http://x",
            f"rm {FILE}",
            f"mv {FILE} /tmp/y",
        ):
            with self.subTest(command=command):
                out = json.loads(run_guard(payload_for(command)).stdout)
                decision = out["hookSpecificOutput"]
                self.assertEqual(decision["permissionDecision"], "ask")
                self.assertEqual(decision["hookEventName"], "PreToolUse")

    def test_other_files_are_not_guarded(self):
        self.assertEqual(run_guard(payload_for("echo x > README.md")).stdout, "")

    def test_malformed_input_never_crashes(self):
        for stdin in ("", "not json", "[]", "null", '{"tool_input": null}', '{"tool_input": {"command": 5}}'):
            with self.subTest(stdin=stdin):
                result = run_guard(stdin)
                self.assertEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")

    def test_real_payload_shape_is_understood(self):
        out = run_guard(payload_for(f"echo x > {FILE}")).stdout
        self.assertIn("ask", out)


if __name__ == "__main__":
    unittest.main()

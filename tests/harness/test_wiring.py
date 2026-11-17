"""Тесты проводки: команды хуков из settings.json запускаются с настоящими данными хуков, git-хук блокирует плохой коммит."""
import json
import os
import re
import subprocess
import unittest

from helpers import FIXTURES, Repo, hook_payload

# событие -> допустимые коды возврата всех его команд на эталонных данных чистой копии
EXPECTED = {
    "PreToolUse": {0},
    "PostToolUse": {0},
    "Stop": {0},
    "SessionStart": {0},
    "SessionEnd": {0},
    "PreCompact": {0},
}


class WiringCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo = Repo()

    @classmethod
    def tearDownClass(cls):
        cls.repo.close()

    def setUp(self):
        self.repo.reset()

    def commands(self, event):
        settings = json.loads(self.repo.read(".claude/settings.json"))
        return [hook["command"] for group in settings["hooks"].get(event, []) for hook in group["hooks"]]

    def run_command(self, command, stdin):
        return subprocess.run(["bash", "-c", command], cwd=self.repo.dir, input=stdin, capture_output=True, text=True,
                              env=self.repo.env())


class TestHookWiring(WiringCase):
    def test_every_configured_event_has_a_fixture_and_an_expectation(self):
        settings = json.loads((self.repo.dir / ".claude/settings.json").read_text(encoding="utf-8"))
        for event in settings["hooks"]:
            with self.subTest(event=event):
                self.assertTrue((FIXTURES / "hooks" / f"{event}.json").exists(), "нет эталонных данных хука")
                self.assertIn(event, EXPECTED, "нет ожидания для события")

    def test_every_hook_command_runs_on_real_payloads(self):
        for event, allowed in EXPECTED.items():
            payload = hook_payload(event, self.repo.dir)
            for command in self.commands(event):
                with self.subTest(event=event, command=command):
                    result = self.run_command(command, payload)
                    self.assertIn(result.returncode, allowed, result.stdout + result.stderr)

    def test_hook_commands_reference_project_scripts_only_through_project_dir(self):
        settings = json.loads((self.repo.dir / ".claude/settings.json").read_text(encoding="utf-8"))
        for event, groups in settings["hooks"].items():
            for group in groups:
                for hook in group["hooks"]:
                    with self.subTest(event=event, command=hook["command"]):
                        self.assertIn("$CLAUDE_PROJECT_DIR", hook["command"])

    def test_pre_tool_use_asks_before_writing_the_constitution(self):
        payload = json.loads(hook_payload("PreToolUse", self.repo.dir))
        payload["tool_input"]["command"] = "echo x >> .specify/memory/constitution.md"
        outputs = [self.run_command(c, json.dumps(payload)).stdout for c in self.commands("PreToolUse")]
        self.assertTrue(any('"permissionDecision": "ask"' in out for out in outputs))

    def test_post_tool_use_reports_a_broken_document(self):
        self.repo.append("README.md", "\n[x](docs/nope.md)\n")
        results = [self.run_command(c, hook_payload("PostToolUse", self.repo.dir)) for c in self.commands("PostToolUse")]
        self.assertIn(2, [r.returncode for r in results])
        self.assertTrue(any("битая ссылка" in r.stderr for r in results))

    def test_session_start_reminds_about_pending_sessions(self):
        self.run_command(self.commands("PreCompact")[0], hook_payload("PreCompact", self.repo.dir))
        outputs = [self.run_command(c, hook_payload("SessionStart", self.repo.dir)).stdout
                   for c in self.commands("SessionStart")]
        self.assertTrue(any("/harvest" in out for out in outputs))

    def test_session_start_enables_the_git_hook(self):
        for command in self.commands("SessionStart"):
            self.run_command(command, hook_payload("SessionStart", self.repo.dir))
        self.assertEqual(self.repo.git("config", "core.hooksPath").stdout.strip(), ".githooks")

    def test_plugin_restrictions_and_permissions_are_present(self):
        settings = json.loads(self.repo.read(".claude/settings.json"))
        self.assertTrue(settings["enabledPlugins"])
        self.assertTrue(all(value is False for value in settings["enabledPlugins"].values()))
        self.assertIn("Edit(./.specify/memory/constitution.md)", settings["permissions"]["ask"])
        self.assertEqual(["Bash(.specify/scripts/bash/*)"], settings["permissions"]["allow"])
        self.assertTrue(any("--no-verify" in rule for rule in settings["permissions"]["deny"]))
        self.assertTrue(any(".env" in rule for rule in settings["permissions"]["deny"]))


class TestSpecKitHooks(WiringCase):
    def test_after_constitution_runs_docs_sync_and_offers_audit(self):
        text = self.repo.read(".specify/extensions.yml")
        self.assertRegex(text, r"(?s)after_constitution:.*command: docs\.sync.*optional: false")
        self.assertRegex(text, r"(?s)command: audit.*optional: true")

    def test_before_plan_asks_about_hot_zones(self):
        self.assertRegex(self.repo.read(".specify/extensions.yml"), r"(?s)before_plan:.*command: hot-zones.*optional: false")

    def test_after_implement_runs_docs_sync(self):
        self.assertRegex(self.repo.read(".specify/extensions.yml"), r"(?s)after_implement:.*command: docs\.sync")

    def test_hook_commands_have_matching_skills(self):
        for name in re.findall(r"^\s*command:\s*(\S+)", self.repo.read(".specify/extensions.yml"), re.M):
            skill = name.replace(".", "-")
            self.assertTrue(self.repo.path(f".claude/skills/{skill}/SKILL.md").exists(), skill)


class TestGitHook(WiringCase):
    def commit(self, message="change"):
        return self.repo.run(["git", "commit", "-q", "-m", message])

    def test_pre_commit_is_executable(self):
        self.assertTrue(os.access(self.repo.path(".githooks/pre-commit"), os.X_OK))

    def test_bad_commit_is_rejected_and_good_commit_passes(self):
        self.repo.git("config", "core.hooksPath", ".githooks")
        self.repo.append("README.md", "\nтест — тире\n")
        self.repo.git("add", "-A")
        rejected = self.commit()
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("docs-lint", rejected.stdout + rejected.stderr)
        self.repo.reset()
        self.repo.git("config", "core.hooksPath", ".githooks")
        self.repo.append("README.md", "\nОбычная строка без запрещенных символов.\n")
        self.repo.git("add", "-A")
        accepted = self.commit()
        self.assertEqual(accepted.returncode, 0, accepted.stdout + accepted.stderr)

    def test_constitution_change_without_version_bump_is_rejected(self):
        self.repo.git("config", "core.hooksPath", ".githooks")
        self.repo.append(".specify/memory/constitution.md", "\nправка без версии\n")
        self.repo.git("add", "-A")
        rejected = self.commit()
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("не больше прежней", rejected.stdout + rejected.stderr)


if __name__ == "__main__":
    unittest.main()

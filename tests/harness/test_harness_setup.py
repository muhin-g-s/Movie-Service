"""Скрипт harness-setup: проверка зависимостей по PATH, команды установки для ОС, включение git-хука,
и скилл harness-setup: README ведет к нему, его команды существуют."""
import json
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from helpers import ROOT, Repo

SCRIPT = ROOT / "tools" / "harness-setup.py"
SKILL = ROOT / ".claude/skills/harness-setup/SKILL.md"
TOOLS = ("git", "bash", "diff", "patch", "make", "python3", "claude", "specify", "uv")
CLAUDE_STUB = """#!/bin/sh
if [ "$1 $2" = "plugin list" ]; then
  echo '[{"id": "oh-my-claudecode@omc", "version": "4.13.7", "enabled": __ENABLED__}]'
else
  echo "2.1.0 (Claude Code)"
fi
"""


class Stubs:
    """Папка с заглушками команд, из которой собирается PATH."""

    def __init__(self, names, omc_enabled=True):
        self.dir = Path(tempfile.mkdtemp(prefix="harness-setup-bin-")).resolve()
        real_git = shutil.which("git")
        for name in names:
            if name == "claude":
                body = CLAUDE_STUB.replace("__ENABLED__", "true" if omc_enabled else "false")
            elif name == "git":
                body = f'#!/bin/sh\nexec "{real_git}" "$@"\n'
            else:
                body = f'#!/bin/sh\necho "{name} version 1.0"\n'
            path = self.dir / name
            path.write_text(body, encoding="utf-8")
            path.chmod(path.stat().st_mode | stat.S_IXUSR)

    def path(self):
        return str(self.dir)

    def close(self):
        shutil.rmtree(self.dir, ignore_errors=True)


class TestCheck(unittest.TestCase):
    def setUp(self):
        self.repo = Repo()
        self.stubs = Stubs(TOOLS)

    def tearDown(self):
        self.repo.close()
        self.stubs.close()

    def check(self, path, os_name="linux-apt"):
        env = self.repo.env({"PATH": path, "HARNESS_SETUP_OS": os_name})
        return subprocess.run([sys.executable, str(self.repo.path("tools/harness-setup.py")), "check"],
                              cwd=self.repo.dir, capture_output=True, text=True, env=env)

    def test_everything_present_passes(self):
        self.repo.git("config", "core.hooksPath", ".githooks")
        done = self.check(self.stubs.path())
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("harness-setup: ok", done.stdout)
        self.assertNotIn("[НЕТ]", done.stdout)

    def test_missing_tools_print_install_commands_for_os(self):
        for name in ("python3", "git", "make"):
            (self.stubs.dir / name).unlink()
        done = self.check(self.stubs.path())
        self.assertEqual(done.returncode, 1)
        self.assertIn("не хватает обязательного", done.stdout)
        self.assertIn("python3", done.stdout)
        self.assertIn("sudo apt-get update && sudo apt-get install -y git make python3", done.stdout)

    def test_windows_commands_use_git_for_windows_and_uv(self):
        for name in ("python3", "git", "make"):
            (self.stubs.dir / name).unlink()
        done = self.check(self.stubs.path(), os_name="windows-winget")
        self.assertIn("winget install --id Git.Git -e", done.stdout)
        self.assertIn("winget install --id GnuWin32.Make -e", done.stdout)
        self.assertIn("uv python install --default", done.stdout)

    def test_missing_claude_is_the_hard_dependency(self):
        (self.stubs.dir / "claude").unlink()
        done = self.check(self.stubs.path())
        self.assertEqual(done.returncode, 1)
        self.assertIn("Сначала установите Claude Code", done.stdout)

    def test_disabled_omc_is_required(self):
        self.stubs.close()
        self.stubs = Stubs(TOOLS, omc_enabled=False)
        done = self.check(self.stubs.path())
        self.assertEqual(done.returncode, 1)
        self.assertIn("claude plugin enable oh-my-claudecode@omc", done.stdout)

    def test_missing_omc_prints_marketplace_and_install(self):
        (self.stubs.dir / "claude").write_text('#!/bin/sh\n[ "$1 $2" = "plugin list" ] && echo "[]" || echo "2.1.0"\n')
        done = self.check(self.stubs.path())
        self.assertEqual(done.returncode, 1)
        self.assertIn("claude plugin marketplace add https://github.com/Yeachan-Heo/oh-my-claudecode", done.stdout)
        self.assertIn("claude plugin install oh-my-claudecode@omc", done.stdout)

    def test_specify_and_uv_are_optional_but_pinned(self):
        for name in ("specify", "uv"):
            (self.stubs.dir / name).unlink()
        self.repo.git("config", "core.hooksPath", ".githooks")
        done = self.check(self.stubs.path())
        self.assertEqual(done.returncode, 0, done.stdout)
        version = json.loads(self.repo.read(".specify/init-options.json"))["speckit_version"]
        self.assertIn(f"uv tool install 'specify-cli=={version}'", done.stdout)
        self.assertIn("curl -LsSf https://astral.sh/uv/install.sh | sh", done.stdout)

    def test_hooks_command_enables_git_hook(self):
        env = self.repo.env({"PATH": self.stubs.path()})
        done = subprocess.run([sys.executable, "tools/harness-setup.py", "hooks"], cwd=self.repo.dir,
                              capture_output=True, text=True, env=env)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.repo.git("config", "core.hooksPath").stdout.strip(), ".githooks")
        again = subprocess.run([sys.executable, "tools/harness-setup.py", "hooks"], cwd=self.repo.dir,
                               capture_output=True, text=True, env=env)
        self.assertIn("уже включен", again.stdout)

    def test_unknown_command_is_usage_error(self):
        done = subprocess.run([sys.executable, str(SCRIPT), "bogus"], capture_output=True, text=True)
        self.assertEqual(done.returncode, 2)


class TestSkill(unittest.TestCase):
    def test_skill_commands_match_script(self):
        text = SKILL.read_text(encoding="utf-8")
        for command in ("python3 tools/harness-setup.py check", "python3 tools/harness-setup.py hooks",
                        "python3 tools/docs-lint.py", "python3 -m unittest discover -s tests/harness"):
            self.assertIn(command, text)
        for script in set(re.findall(r"python3 (tools/[\w-]+\.py)", text)):
            self.assertTrue((ROOT / script).exists(), script)

    def test_readme_quick_start_points_to_setup(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("## Быстрый старт харнеса", readme)
        self.assertIn("`/harness-setup`", readme)
        self.assertIn("python3 tools/harness-setup.py check", readme)


if __name__ == "__main__":
    unittest.main()

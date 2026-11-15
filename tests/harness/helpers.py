"""Общие помощники тестов харнеса. Нужен только Python 3, сторонних пакетов нет."""
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "harness" / "fixtures"
CONSTITUTION = ".specify/memory/constitution.md"


def load_tool(name):
    """Загружает скрипт из tools/ как модуль."""
    spec = importlib.util.spec_from_file_location("tool_" + name.replace("-", "_"), ROOT / "tools" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def hook_payload(event, root):
    """Эталонные данные хука, в которых подставлен путь рабочей копии."""
    return (FIXTURES / "hooks" / f"{event}.json").read_text(encoding="utf-8").replace("__ROOT__", str(root))


class Repo:
    """Временная копия рабочего состояния репозитория с git и базовым коммитом.

    Настоящие файлы и настоящая домашняя папка пользователя не затрагиваются.
    """

    def __init__(self):
        self.dir = Path(tempfile.mkdtemp(prefix="harness-test-")).resolve()
        self.home = Path(tempfile.mkdtemp(prefix="harness-home-")).resolve()
        files = subprocess.run(["git", "ls-files", "-co", "--exclude-standard"], cwd=ROOT,
                               capture_output=True, text=True).stdout.split("\n")
        for rel in filter(None, files):
            src = ROOT / rel
            if src.is_file():
                (self.dir / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, self.dir / rel)
        self.git("init", "-q")
        self.git("config", "user.name", "test")
        self.git("config", "user.email", "test@test")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "base", "--no-verify")

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.dir, capture_output=True, text=True)

    def path(self, rel):
        return self.dir / rel

    def read(self, rel):
        return (self.dir / rel).read_text(encoding="utf-8")

    def write(self, rel, text):
        target = self.dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    def append(self, rel, text):
        self.write(rel, self.read(rel) + text)

    def replace(self, rel, old, new, count=1):
        text = self.read(rel)
        assert old in text, f"в {rel} нет текста {old!r}"
        self.write(rel, text.replace(old, new, count))

    def env(self, extra=None):
        return {**os.environ, "HOME": str(self.home), "CLAUDE_PROJECT_DIR": str(self.dir), **(extra or {})}

    def run(self, args, stdin=None, extra_env=None):
        return subprocess.run(args, cwd=self.dir, input=stdin, capture_output=True, text=True,
                              env=self.env(extra_env))

    def lint(self, *flags, stdin=None):
        return self.run(["python3", "tools/docs-lint.py", *flags], stdin=stdin)

    def reset(self):
        self.git("reset", "-q", "--hard")
        self.git("clean", "-fdq")
        self.git("config", "--unset", "core.hooksPath")

    def close(self):
        shutil.rmtree(self.dir, ignore_errors=True)
        shutil.rmtree(self.home, ignore_errors=True)

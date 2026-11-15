"""Тесты tools/harvest.py: читает только папку этого проекта и находит повторяющийся контекст."""
import json
import re
import unittest

from helpers import Repo


def user_row(text, sidechain=False):
    return json.dumps({"type": "user", "isSidechain": sidechain, "message": {"role": "user", "content": text}},
                      ensure_ascii=False)


class HarvestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo = Repo()
        encoded = re.sub(r"[^A-Za-z0-9]", "-", str(cls.repo.dir))
        cls.project = cls.repo.home / ".claude" / "projects" / encoded
        cls.project.mkdir(parents=True)

    @classmethod
    def tearDownClass(cls):
        cls.repo.close()

    def setUp(self):
        for path in self.project.glob("*"):
            if path.is_file():
                path.unlink()

    def sessions(self, **files):
        for name, rows in files.items():
            (self.project / f"{name}.jsonl").write_text("\n".join(rows) + "\n", encoding="utf-8")

    def harvest(self, *flags):
        return self.repo.run(["python3", "tools/harvest.py", *flags]).stdout


class TestHarvest(HarvestCase):
    def test_finds_phrase_repeated_across_messages(self):
        phrase = "всегда пиши ответы пользователю только на русском языке без исключений"
        self.sessions(a=[user_row(phrase + " один"), user_row("другое сообщение совсем про другое"),
                         user_row(phrase + " два")])
        out = self.harvest("--words", "6")
        self.assertIn("повторов: 2", out)
        self.assertIn("всегда пиши ответы", out)

    def test_phrase_already_in_documentation_is_not_reported(self):
        agents = self.repo.read("AGENTS.md")
        sentence = re.search(r"Если задача упирается в тему из реестра отложенных решений, остановись и сообщи об этом", agents)
        self.assertIsNotNone(sentence)
        self.sessions(a=[user_row(sentence.group(0) + " раз"), user_row(sentence.group(0) + " два")])
        self.assertIn("которых нет в документации репозитория: 0", self.harvest("--words", "5"))

    def test_harness_noise_and_sidechains_are_ignored(self):
        noise = "Another Claude session sent a message: повтор повтор повтор повтор повтор повтор повтор"
        self.sessions(a=[user_row(noise), user_row(noise),
                         user_row("повтор повтор повтор повтор повтор повтор повтор", sidechain=True),
                         user_row("повтор повтор повтор повтор повтор повтор повтор", sidechain=True)])
        out = self.harvest("--words", "5")
        self.assertIn("Реплик пользователя: 0", out)

    def test_system_reminder_blocks_are_stripped(self):
        block = "<system-reminder>служебный текст служебный текст служебный текст служебный текст служебный</system-reminder>"
        self.sessions(a=[user_row(block + "уникальная реплика номер один"), user_row(block + "уникальная реплика номер два")])
        self.assertNotIn("служебный", self.harvest("--words", "3"))

    def test_warns_when_transcript_format_is_unknown(self):
        self.sessions(a=['{"kind": "x", "payload": 1}'])
        self.assertIn("ПРЕДУПРЕЖДЕНИЕ", self.harvest())

    def test_lists_claude_memory_files(self):
        memory = self.project / "memory"
        memory.mkdir()
        (memory / "note.md").write_text("# Факт о проекте\nтекст\n", encoding="utf-8")
        try:
            self.assertIn("note.md", self.harvest())
        finally:
            (memory / "note.md").unlink()
            memory.rmdir()

    def test_reads_only_this_projects_folder(self):
        other = self.repo.home / ".claude" / "projects" / "-some-other-project"
        other.mkdir(parents=True)
        (other / "x.jsonl").write_text(user_row("чужая реплика про совсем другой проект раз два") + "\n" +
                                       user_row("чужая реплика про совсем другой проект раз два") + "\n", encoding="utf-8")
        self.sessions(a=[user_row("своя единственная реплика")])
        out = self.harvest("--words", "3")
        self.assertIn("Реплик пользователя: 1", out)
        self.assertNotIn("чужая", out)

    def test_never_writes_anything(self):
        self.sessions(a=[user_row("любая реплика")])
        before = sorted(str(p) for p in self.repo.dir.rglob("*") if ".git" not in p.parts)
        self.harvest()
        after = sorted(str(p) for p in self.repo.dir.rglob("*") if ".git" not in p.parts)
        self.assertEqual(before, after)

    def test_missing_project_folder_is_handled(self):
        import shutil
        shutil.rmtree(self.project)
        try:
            out = self.harvest()
            self.assertIn("Реплик пользователя: 0", out)
        finally:
            self.project.mkdir(parents=True)


if __name__ == "__main__":
    unittest.main()

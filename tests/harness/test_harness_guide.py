"""Скилл harness-guide: упоминает каждый скилл репозитория, ссылается только на существующие скиллы и скрипты,
README ведет к нему и содержит быстрый старт харнеса."""
import re
import unittest

from helpers import ROOT

SKILL = ROOT / ".claude/skills/harness-guide/SKILL.md"
SKILLS = ROOT / ".claude/skills"


def mentioned_skills(text):
    return set(re.findall(r"`/([a-z0-9-]+)`", text))


class TestHarnessGuide(unittest.TestCase):
    def setUp(self):
        self.text = SKILL.read_text(encoding="utf-8")
        self.existing = {p.parent.name for p in SKILLS.glob("*/SKILL.md")}

    def test_mentions_every_skill(self):
        missing = sorted(self.existing - mentioned_skills(self.text))
        self.assertEqual(missing, [], "скилл не упомянут в harness-guide")

    def test_mentions_only_existing_skills(self):
        unknown = sorted(mentioned_skills(self.text) - self.existing)
        self.assertEqual(unknown, [], "harness-guide ссылается на несуществующий скилл")

    def test_mentioned_scripts_exist(self):
        scripts = set(re.findall(r"python3 (tools/[\w-]+\.py)", self.text))
        self.assertTrue(scripts, "harness-guide должен брать состояние командами скриптов")
        for script in sorted(scripts):
            self.assertTrue((ROOT / script).exists(), script)

    def test_guide_is_read_only(self):
        self.assertIn("Файлы не правь", self.text)

    def test_readme_points_to_guide_and_quick_start(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("`/harness-guide`", readme)
        self.assertIn("## Быстрый старт харнеса", readme)
        self.assertIn("`/harness-setup`", readme)


if __name__ == "__main__":
    unittest.main()

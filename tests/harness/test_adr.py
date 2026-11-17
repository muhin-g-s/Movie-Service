"""Тесты tools/adr.py: создание ADR по шаблону и перевод в принятые."""
import re
import unittest

from helpers import CONSTITUTION, Repo

DECISIONS = "docs/decisions"
INDEX = f"{DECISIONS}/README.md"


class AdrCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo = Repo()

    @classmethod
    def tearDownClass(cls):
        cls.repo.close()

    def setUp(self):
        self.repo.reset()

    def adr(self, *args):
        return self.repo.run(["python3", "tools/adr.py", *args])

    def new(self, slug="demo-choice", title="Выбор демонстрации"):
        return self.adr("new", "--slug", slug, "--title", title)

    def ok(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def refused(self, result, expected):
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn(expected, result.stderr)

    def numbers(self):
        return sorted(p.name[:4] for p in self.repo.path(DECISIONS).glob("[0-9][0-9][0-9][0-9]-*.md"))

    def next_number(self):
        return f"{int(self.numbers()[-1]) + 1:04d}"

    def version(self):
        return re.search(r"\*\*Версия\*\*: (\d+\.\d+\.\d+)", self.repo.read(CONSTITUTION)).group(1)

    def lint_ok(self):
        result = self.repo.lint()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class TestNew(AdrCase):
    def test_new_creates_numbered_file_with_header_and_sections(self):
        number = self.next_number()
        self.ok(self.new())
        text = self.repo.read(f"{DECISIONS}/{number}-demo-choice.md")
        self.assertTrue(text.startswith(f"# ADR {number}: Выбор демонстрации"))
        for marker in ("> **Отвечает за:**", "> **Не содержит:**", "> **Читать когда:**",
                       f"> **Проверено при версии конституции:** {self.version()}", "**Статус:** предложено,"):
            self.assertIn(marker, text)
        for section in ("## Контекст", "## Решение", "## Рассмотренные варианты", "## Последствия",
                        "## Связанные файлы"):
            self.assertIn(section, text)

    def test_new_adds_row_to_the_index_and_keeps_lint_clean(self):
        number = self.next_number()
        self.ok(self.new())
        self.assertIn(f"| [{number}]({number}-demo-choice.md) | Выбор демонстрации | предложено |", self.repo.read(INDEX))
        self.lint_ok()

    def test_numbers_are_sequential(self):
        first = self.next_number()
        self.ok(self.new("one", "Первый"))
        self.ok(self.new("two", "Второй"))
        self.assertEqual(self.numbers()[-2:], [first, f"{int(first) + 1:04d}"])

    def test_slug_must_be_latin(self):
        self.refused(self.new("выбор"), "slug")

    def test_title_is_required(self):
        self.assertEqual(self.adr("new", "--slug", "x").returncode, 2)

    def test_duplicate_slug_is_refused(self):
        self.ok(self.new())
        self.refused(self.new(), "уже есть")


class TestAccept(AdrCase):
    def fill(self, path):
        text = self.repo.read(path).replace("(заполнить)", "Текст.")
        self.repo.write(path, text)

    def test_accept_updates_status_and_index(self):
        number = self.next_number()
        self.ok(self.new())
        path = f"{DECISIONS}/{number}-demo-choice.md"
        self.fill(path)
        self.ok(self.adr("accept", number))
        self.assertIn("**Статус:** принято,", self.repo.read(path))
        self.assertIn(f"| [{number}]({number}-demo-choice.md) | Выбор демонстрации | принято |", self.repo.read(INDEX))
        self.lint_ok()

    def test_accept_refuses_unfilled_template(self):
        number = self.next_number()
        self.ok(self.new())
        self.refused(self.adr("accept", number), "заполнить")

    def test_accept_unknown_number(self):
        self.refused(self.adr("accept", "9999"), "нет ADR")

    def test_accept_twice_is_refused(self):
        number = self.next_number()
        self.ok(self.new())
        self.fill(f"{DECISIONS}/{number}-demo-choice.md")
        self.ok(self.adr("accept", number))
        self.refused(self.adr("accept", number), "уже принят")


class TestCli(AdrCase):
    def test_unknown_command(self):
        self.assertEqual(self.adr("nothing").returncode, 2)

    def test_no_command(self):
        self.assertEqual(self.adr().returncode, 2)


if __name__ == "__main__":
    unittest.main()

"""Тесты tools/stage.py: этапы разработки как аннотированные теги git с проверкой готовности."""
import unittest

from helpers import Repo


class StageCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo = Repo()
        cls.base = cls.repo.git("rev-parse", "HEAD").stdout.strip()

    @classmethod
    def tearDownClass(cls):
        cls.repo.close()

    def setUp(self):
        self.repo.reset()
        self.repo.git("checkout", "-q", "-B", "main", self.base)
        for tag in self.repo.git("tag", "-l").stdout.split():
            self.repo.git("tag", "-d", tag)

    def stage(self, *args, env=None):
        return self.repo.run(["python3", "tools/stage.py", *args], extra_env=env)

    def close(self, slug="first", title="First stage", *extra, env=None):
        return self.stage("close", "--slug", slug, "--title", title, *extra, env=env)

    def ok(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def refused(self, result, expected):
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn(expected, result.stdout + result.stderr)

    def commit(self, name="x.txt"):
        self.repo.write(name, "x\n")
        self.repo.git("add", "-A")
        self.repo.git("commit", "-q", "-m", "more", "--no-verify")

    def tags(self):
        return self.repo.git("tag", "-l", "stage/*").stdout.split()

    def done_epic(self):
        self.repo.write("specs/001-a/spec.md", "# S\n\n**Control level**: контрольная точка\n")
        self.repo.write("specs/001-a/tasks.md", "- [x] T001 Тест\n")
        self.repo.write("work/W001-mvp.md", "---\nid: W001\ntitle: MVP\ntype: эпик\npriority: P1\n---\n")
        self.repo.write("work/W002-a.md", "---\nid: W002\ntitle: A\ntype: фича\nparent: W001\nkind: простой\n"
                                          "priority: P1\ncontrol: контрольная точка\nspec: specs/001-a\n---\n")
        self.repo.git("add", "-A")
        self.repo.git("commit", "-q", "-m", "work", "--no-verify")


class TestList(StageCase):
    def test_no_stages_yet(self):
        result = self.ok(self.stage("list"))
        self.assertIn("этапов еще нет", result.stdout)

    def test_list_shows_closed_stage_and_commits_after_it(self):
        self.ok(self.close())
        self.commit()
        self.commit("y.txt")
        out = self.ok(self.stage("list")).stdout
        self.assertIn("01 first", out)
        self.assertIn("First stage", out)
        self.assertIn("после этапа 01 коммитов: 2", out)


class TestClose(StageCase):
    def test_close_creates_annotated_numbered_tag(self):
        result = self.ok(self.close())
        self.assertEqual(self.tags(), ["stage/01-first"])
        self.assertEqual(self.repo.git("cat-file", "-t", "stage/01-first").stdout.strip(), "tag")
        message = self.repo.git("tag", "-n99", "stage/01-first").stdout
        self.assertIn("First stage", message)
        self.assertIn("constitution:", message)
        self.assertIn("не выполнена", result.stdout)

    def test_second_stage_gets_next_number(self):
        self.ok(self.close())
        self.commit()
        self.ok(self.close("second", "Second stage"))
        self.assertEqual(self.tags(), ["stage/01-first", "stage/02-second"])

    def test_needs_new_commits_after_previous_stage(self):
        self.ok(self.close())
        self.refused(self.close("second", "Second"), "нет новых коммитов")

    def test_dirty_tree_is_refused(self):
        self.repo.write("dirty.txt", "x\n")
        self.refused(self.close(), "не чистое")
        self.assertEqual(self.tags(), [])

    def test_wrong_branch_is_refused(self):
        self.repo.git("checkout", "-q", "-b", "feature")
        self.refused(self.close(), "main или master")

    def test_broken_documentation_is_refused(self):
        self.repo.append("README.md", "\n[x](docs/nope.md)\n")
        self.repo.git("add", "-A")
        self.repo.git("commit", "-q", "-m", "break", "--no-verify")
        self.refused(self.close(), "проверка документации не пройдена")
        self.assertEqual(self.tags(), [])

    def test_slug_must_be_latin(self):
        self.refused(self.close("первый"), "slug")

    def test_duplicate_slug_is_refused(self):
        self.ok(self.close())
        self.commit()
        self.refused(self.close("first", "Again"), "уже есть")

    def test_title_is_required(self):
        self.assertEqual(self.stage("close", "--slug", "first").returncode, 2)

    def test_failing_tests_block_closing_with_flag(self):
        self.refused(self.close("first", "T", "--with-tests", env={"STAGE_TEST_COMMAND": "false"}), "тесты не пройдены")
        self.assertEqual(self.tags(), [])

    def test_passing_tests_allow_closing_with_flag(self):
        self.ok(self.close("first", "T", "--with-tests", env={"STAGE_TEST_COMMAND": "true"}))
        self.assertEqual(self.tags(), ["stage/01-first"])

    def test_done_epic_is_recorded(self):
        self.done_epic()
        self.ok(self.close("mvp", "MVP", "--epics", "W001"))
        self.assertIn("epics: W001", self.repo.git("tag", "-n99", "stage/01-mvp").stdout)

    def test_unfinished_epic_blocks_closing(self):
        self.repo.write("work/W001-mvp.md", "---\nid: W001\ntitle: MVP\ntype: эпик\npriority: P1\n---\n")
        self.repo.git("add", "-A")
        self.repo.git("commit", "-q", "-m", "work", "--no-verify")
        self.refused(self.close("mvp", "MVP", "--epics", "W001"), "эпик W001 не готов")

    def test_unknown_epic_blocks_closing(self):
        self.refused(self.close("mvp", "MVP", "--epics", "W404"), "нет эпика W404")


class TestShowAndCli(StageCase):
    def test_show_prints_the_annotation(self):
        self.ok(self.close())
        out = self.ok(self.stage("show", "01")).stdout
        self.assertIn("stage/01-first", out)
        self.assertIn("First stage", out)

    def test_show_unknown_stage(self):
        self.assertEqual(self.stage("show", "09").returncode, 2)

    def test_unknown_command(self):
        self.assertEqual(self.stage("nothing").returncode, 2)

    def test_no_command(self):
        self.assertEqual(self.stage().returncode, 2)


if __name__ == "__main__":
    unittest.main()

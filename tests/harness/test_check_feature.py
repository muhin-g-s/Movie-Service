"""Тесты tools/check-feature.py: детерминированная проверка артефактов фичи между шагами цепочки."""
import json
import unittest

from helpers import Repo

FEATURE = "specs/001-demo"
GOOD_SPEC = "# Спецификация\n\nПользователь открывает страницу и видит список.\n"
GOOD_TASKS = "# Задачи\n\n- [ ] T001 Тест списка\n- [ ] T002 [P] Реализация списка\n"


class CheckCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo = Repo()

    @classmethod
    def tearDownClass(cls):
        cls.repo.close()

    def setUp(self):
        self.repo.reset()

    def point_to(self, value=FEATURE):
        self.repo.write(".specify/feature.json", json.dumps({"feature_directory": value}))

    def version(self):
        text = self.repo.read(".specify/memory/constitution.md")
        return text.split("**Версия**: ")[1].split(" ")[0]

    def check(self, stage):
        return self.repo.run(["python3", "tools/check-feature.py", stage])

    def ok(self, stage):
        result = self.check(stage)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def fails(self, stage, expected, code=1):
        result = self.check(stage)
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        self.assertIn(expected, result.stderr)


class TestFeatureLocation(CheckCase):
    def test_no_pointer_to_feature(self):
        self.fails("spec", "не найден каталог фичи", code=2)

    def test_pointer_to_missing_directory(self):
        self.point_to("specs/999-none")
        self.fails("spec", "не найден каталог фичи", code=2)

    def test_unknown_stage(self):
        self.point_to()
        self.repo.write(f"{FEATURE}/spec.md", GOOD_SPEC)
        self.fails("nothing", "неизвестный этап", code=2)

    def test_absolute_and_relative_pointers_are_accepted(self):
        self.repo.write(f"{FEATURE}/spec.md", GOOD_SPEC)
        self.point_to(FEATURE)
        self.ok("spec")
        self.point_to(str(self.repo.path(FEATURE)))
        self.ok("spec")


class TestSpecStage(CheckCase):
    def setUp(self):
        super().setUp()
        self.point_to()

    def test_missing_spec(self):
        self.repo.path(FEATURE).mkdir(parents=True)
        self.fails("spec", "нет файла spec.md")

    def test_empty_spec(self):
        self.repo.write(f"{FEATURE}/spec.md", "  \n")
        self.fails("spec", "spec.md пуст")

    def test_spec_with_open_clarification_markers(self):
        self.repo.write(f"{FEATURE}/spec.md", GOOD_SPEC + "\n[NEEDS CLARIFICATION: срок хранения]\n")
        self.fails("spec", "остались маркеры уточнения")

    def test_good_spec(self):
        self.repo.write(f"{FEATURE}/spec.md", GOOD_SPEC)
        self.ok("spec")


class TestPlanStage(CheckCase):
    def setUp(self):
        super().setUp()
        self.point_to()

    def test_missing_plan(self):
        self.repo.path(FEATURE).mkdir(parents=True)
        self.fails("plan", "нет файла plan.md")

    def test_plan_without_constitution_version(self):
        self.repo.write(f"{FEATURE}/plan.md", "# План\n")
        self.fails("plan", "нет строки Constitution version")

    def test_plan_with_stale_constitution_version(self):
        self.repo.write(f"{FEATURE}/plan.md", "# План\n\n**Constitution version**: 1.0.0\n")
        self.fails("plan", "это старше версии")

    def test_good_plan(self):
        self.repo.write(f"{FEATURE}/plan.md", f"# План\n\n**Constitution version**: {self.version()}\n")
        self.ok("plan")


class TestTasksStages(CheckCase):
    def setUp(self):
        super().setUp()
        self.point_to()

    def test_missing_tasks(self):
        self.repo.path(FEATURE).mkdir(parents=True)
        self.fails("tasks", "нет файла tasks.md")

    def test_tasks_without_task_lines(self):
        self.repo.write(f"{FEATURE}/tasks.md", "# Задачи\n\nпусто\n")
        self.fails("tasks", "в tasks.md нет задач")

    def test_good_tasks(self):
        self.repo.write(f"{FEATURE}/tasks.md", GOOD_TASKS)
        self.ok("tasks")

    def test_done_fails_while_tasks_are_open(self):
        self.repo.write(f"{FEATURE}/tasks.md", GOOD_TASKS.replace("- [ ] T001", "- [x] T001"))
        self.fails("done", "не выполнены задачи: T002")

    def test_done_passes_when_all_tasks_are_checked(self):
        self.repo.write(f"{FEATURE}/tasks.md", GOOD_TASKS.replace("- [ ]", "- [x]"))
        self.ok("done")


if __name__ == "__main__":
    unittest.main()

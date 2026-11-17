"""Тесты tools/work.py: файловый трекер работы, вывод статуса из артефактов Spec Kit и проверки связности."""
import unittest

from helpers import Repo

HEAD = ["id", "title", "type", "parent", "depends", "kind", "priority", "control", "spec", "resolves", "result", "status"]


class WorkCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo = Repo()

    @classmethod
    def tearDownClass(cls):
        cls.repo.close()

    def setUp(self):
        self.repo.reset()

    def put(self, ident, slug, **fields):
        data = {"id": ident, "title": "Заголовок", "type": "фича", "kind": "простой", "priority": "P1",
                "control": "контрольная точка", "status": "идея"}
        data.update(fields)
        lines = [f"{key}: {data[key]}" for key in HEAD if data.get(key) is not None]
        self.repo.write(f"work/{ident}-{slug}.md", "---\n" + "\n".join(lines) + "\n---\n\nОписание.\n")

    def epic(self, ident="W001", slug="mvp", **fields):
        self.put(ident, slug, type="эпик", kind=None, control=None, status=None, **fields)

    def spec(self, name="001-demo", plan=False, tasks=None):
        self.repo.write(f"specs/{name}/spec.md", "# Спецификация\n")
        if plan:
            self.repo.write(f"specs/{name}/plan.md", "# План\n")
        if tasks is not None:
            self.repo.write(f"specs/{name}/tasks.md", tasks)

    def work(self, *args):
        return self.repo.run(["python3", "tools/work.py", *args])

    def clean(self):
        result = self.work("check")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def detected(self, expected):
        result = self.work("check")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(expected, result.stdout)
        return result

    def status_of(self, ident):
        result = self.work("show", ident)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        line = next(l for l in result.stdout.splitlines() if l.startswith("статус:"))
        return line.split(":", 1)[1].strip()


class TestCheck(WorkCase):
    def test_empty_tracker_is_clean(self):
        self.clean()

    def test_valid_epic_with_feature_is_clean(self):
        self.epic()
        self.put("W002", "list", parent="W001")
        self.clean()

    def test_duplicate_id(self):
        self.put("W001", "a")
        self.put("W001", "b")
        self.detected("повторяется id W001")

    def test_file_name_must_start_with_id(self):
        self.repo.write("work/W002-x.md", "---\nid: W001\ntitle: T\ntype: задача\npriority: P1\nstatus: идея\n---\n")
        self.detected("имя файла должно начинаться с id W001-")

    def test_bad_id_format(self):
        self.repo.write("work/X1-x.md", "---\nid: X1\ntitle: T\ntype: задача\npriority: P1\nstatus: идея\n---\n")
        self.detected("id должен быть вида W001")

    def test_missing_front_matter(self):
        self.repo.write("work/W001-x.md", "просто текст\n")
        self.detected("нет шапки")

    def test_missing_required_field(self):
        self.put("W001", "a", title=None)
        self.detected("нет поля title")

    def test_unknown_type(self):
        self.put("W001", "a", type="мечта")
        self.detected("неизвестный тип мечта")

    def test_unknown_kind(self):
        self.put("W001", "a", kind="сложный")
        self.detected("неизвестный вид сложный")

    def test_unknown_priority(self):
        self.put("W001", "a", priority="P9")
        self.detected("неизвестный приоритет P9")

    def test_unknown_control_level(self):
        self.put("W001", "a", control="свободный")
        self.detected("неизвестный уровень контроля свободный")

    def test_unknown_manual_status(self):
        self.put("W001", "a", status="почти")
        self.detected("неизвестный статус почти")

    def test_parent_must_exist(self):
        self.put("W002", "a", parent="W009")
        self.detected("parent W009 не найден")

    def test_parent_must_be_an_epic(self):
        self.put("W001", "a")
        self.put("W002", "b", parent="W001")
        self.detected("parent W001 не эпик")

    def test_depends_must_exist(self):
        self.put("W002", "a", depends="[W009]")
        self.detected("depends W009 не найден")

    def test_depends_on_itself(self):
        self.put("W002", "a", depends="[W002]")
        self.detected("зависит от самого себя")

    def test_dependency_cycle(self):
        self.put("W001", "a", depends="[W002]")
        self.put("W002", "b", depends="[W001]")
        self.detected("цикл зависимостей")

    def test_parent_cycle(self):
        self.epic("W001", "a", parent="W002")
        self.epic("W002", "b", parent="W001")
        self.detected("цикл родителей")

    def test_spec_directory_must_exist(self):
        self.put("W001", "a", spec="specs/001-none", status=None)
        self.detected("каталог spec не найден")

    def test_spec_cannot_be_shared(self):
        self.spec()
        self.put("W001", "a", spec="specs/001-demo", status=None)
        self.put("W002", "b", spec="specs/001-demo", status=None)
        self.detected("spec specs/001-demo уже привязан к W001")

    def test_feature_with_spec_has_no_manual_status(self):
        self.spec()
        self.put("W001", "a", spec="specs/001-demo", status="в работе")
        self.detected("у фичи со spec ручной статус")

    def test_feature_with_spec_may_be_cancelled_or_blocked(self):
        self.spec()
        self.put("W001", "a", spec="specs/001-demo", status="отменено")
        self.clean()
        self.put("W001", "a", spec="specs/001-demo", status="заблокировано")
        self.clean()

    def test_task_needs_manual_status(self):
        self.put("W001", "a", type="задача", status=None)
        self.detected("нужен ручной статус")

    def test_research_feature_cannot_be_autonomous(self):
        self.put("W001", "a", kind="исследовательский", control="автономный")
        self.detected("не может быть автономным")

    def test_foundation_feature_cannot_be_autonomous(self):
        self.put("W001", "a", kind="фундамент", control="автономный")
        self.detected("не может быть автономным")

    def test_feature_needs_kind_and_control(self):
        self.put("W001", "a", kind=None)
        self.detected("нет поля kind")
        self.put("W001", "a", control=None)
        self.detected("нет поля control")

    def test_feature_directory_without_item_is_orphan(self):
        self.spec("001-lonely")
        self.detected("specs/001-lonely не привязана к элементу work/")

    def test_linked_feature_is_not_orphan(self):
        self.spec()
        self.put("W001", "a", spec="specs/001-demo", status=None)
        self.clean()


class TestDerivedStatus(WorkCase):
    def feature(self, **kwargs):
        self.spec(**kwargs)
        self.put("W001", "a", spec="specs/001-demo", status=None)

    def test_manual_status_without_spec(self):
        self.put("W001", "a", status="готово к работе")
        self.assertEqual(self.status_of("W001"), "готово к работе")

    def test_spec_only(self):
        self.feature()
        self.assertEqual(self.status_of("W001"), "спецификация")

    def test_plan(self):
        self.feature(plan=True)
        self.assertEqual(self.status_of("W001"), "план")

    def test_tasks_not_started(self):
        self.feature(plan=True, tasks="- [ ] T001 Тест\n- [ ] T002 Код\n")
        self.assertEqual(self.status_of("W001"), "задачи (0/2)")

    def test_tasks_in_progress(self):
        self.feature(plan=True, tasks="- [x] T001 Тест\n- [ ] T002 Код\n")
        self.assertEqual(self.status_of("W001"), "в работе (1/2)")

    def test_tasks_done(self):
        self.feature(plan=True, tasks="- [x] T001 Тест\n- [X] T002 Код\n")
        self.assertEqual(self.status_of("W001"), "готово")

    def test_cancelled_overrides_derived(self):
        self.spec(plan=True, tasks="- [x] T001 Тест\n")
        self.put("W001", "a", spec="specs/001-demo", status="отменено")
        self.assertEqual(self.status_of("W001"), "отменено")

    def test_epic_without_children_is_not_started(self):
        self.epic()
        self.assertEqual(self.status_of("W001"), "не начат (0/0)")

    def test_epic_progress_counts_done_children(self):
        self.epic()
        self.spec("001-a", plan=True, tasks="- [x] T001 Тест\n")
        self.put("W002", "a", parent="W001", spec="specs/001-a", status=None)
        self.put("W003", "b", parent="W001", status="готово к работе")
        self.put("W004", "c", parent="W001", status="отменено")
        self.assertEqual(self.status_of("W001"), "в работе (1/2)")

    def test_epic_is_done_when_all_children_are_done(self):
        self.epic()
        self.spec("001-a", plan=True, tasks="- [x] T001 Тест\n")
        self.put("W002", "a", parent="W001", spec="specs/001-a", status=None)
        self.assertEqual(self.status_of("W001"), "готово (1/1)")

    def test_board_lists_epics_and_children(self):
        self.epic()
        self.put("W002", "a", parent="W001", status="готово к работе")
        self.put("W003", "loose", type="задача", status="идея")
        result = self.work("board")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("W001", result.stdout)
        self.assertIn("  W002", result.stdout)
        self.assertIn("без эпика", result.stdout)
        self.assertIn("W003", result.stdout)

    def test_show_unknown_item(self):
        self.assertEqual(self.work("show", "W404").returncode, 2)


class TestNext(WorkCase):
    def names(self):
        result = self.work("next")
        self.assertEqual(result.returncode, 0, result.stderr)
        return [line.split()[0] for line in result.stdout.splitlines() if line.startswith("W")]

    def test_ready_items_are_listed_by_priority_then_id(self):
        self.put("W001", "a", status="готово к работе", priority="P2")
        self.put("W002", "b", status="готово к работе", priority="P1")
        self.put("W003", "c", status="готово к работе", priority="P1")
        self.assertEqual(self.names(), ["W002", "W003", "W001"])

    def test_idea_is_not_offered(self):
        self.put("W001", "a", status="идея")
        self.assertEqual(self.names(), [])

    def test_unmet_dependency_blocks_item(self):
        self.put("W001", "a", status="готово к работе")
        self.put("W002", "b", status="готово к работе", depends="[W001]")
        self.assertEqual(self.names(), ["W001"])

    def test_done_dependency_unblocks_item(self):
        self.spec(plan=True, tasks="- [x] T001 Тест\n")
        self.put("W001", "a", spec="specs/001-demo", status=None)
        self.put("W002", "b", status="готово к работе", depends="[W001]")
        self.assertEqual(self.names(), ["W002"])

    def test_item_in_progress_is_not_offered(self):
        self.spec(plan=True, tasks="- [ ] T001 Тест\n")
        self.put("W001", "a", spec="specs/001-demo", status=None)
        self.assertEqual(self.names(), [])

    def test_epic_is_never_offered(self):
        self.epic()
        self.assertEqual(self.names(), [])


class TestNewAndLink(WorkCase):
    def new(self, *args):
        return self.work("new", *args)

    def test_new_epic_gets_next_id_and_sections(self):
        self.put("W004", "old", status="идея")
        result = self.new("--type", "эпик", "--title", "MVP", "--slug", "mvp")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        text = self.repo.read("work/W005-mvp.md")
        for expected in ("id: W005", "type: эпик", "## Цель", "## Вне границ", "## Критерии готовности"):
            self.assertIn(expected, text)
        self.clean()

    def test_new_feature_with_relations(self):
        self.epic()
        self.put("W002", "base", status="идея")
        result = self.new("--type", "фича", "--title", "Список", "--slug", "list", "--parent", "W001",
                          "--depends", "W002", "--kind", "простой", "--priority", "P2",
                          "--control", "контрольная точка")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        text = self.repo.read("work/W003-list.md")
        self.assertIn("parent: W001", text)
        self.assertIn("depends: [W002]", text)
        self.assertIn("status: идея", text)
        self.assertIn("## Критерии приемки", text)
        self.clean()

    def test_new_rejects_non_latin_slug(self):
        result = self.new("--type", "задача", "--title", "T", "--slug", "список")
        self.assertEqual(result.returncode, 2)
        self.assertIn("slug", result.stderr)

    def test_new_rejects_invalid_item_and_writes_nothing(self):
        result = self.new("--type", "фича", "--title", "T", "--slug", "x", "--parent", "W009",
                          "--kind", "простой", "--control", "контрольная точка")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(list(self.repo.path("work").glob("W*.md")) if self.repo.path("work").exists() else [], [])

    def test_new_needs_title_and_type(self):
        self.assertEqual(self.new("--slug", "x").returncode, 2)

    def test_link_sets_spec_and_drops_manual_status(self):
        self.spec()
        self.put("W001", "a", status="готово к работе")
        result = self.work("link", "W001", "specs/001-demo")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        text = self.repo.read("work/W001-a.md")
        self.assertIn("spec: specs/001-demo", text)
        self.assertNotIn("status:", text)
        self.assertEqual(self.status_of("W001"), "спецификация")
        self.clean()

    def test_link_needs_existing_feature_directory(self):
        self.put("W001", "a", status="готово к работе")
        self.assertEqual(self.work("link", "W001", "specs/001-none").returncode, 2)

    def test_link_refuses_item_that_already_has_a_spec(self):
        self.spec()
        self.put("W001", "a", spec="specs/001-demo", status=None)
        self.assertEqual(self.work("link", "W001", "specs/001-demo").returncode, 2)

    def test_link_refuses_spec_owned_by_another_item(self):
        self.spec()
        self.put("W001", "a", spec="specs/001-demo", status=None)
        self.put("W002", "b", status="готово к работе")
        self.assertEqual(self.work("link", "W002", "specs/001-demo").returncode, 2)

    def test_link_only_for_features(self):
        self.spec()
        self.put("W001", "a", type="задача", status="идея")
        self.assertEqual(self.work("link", "W001", "specs/001-demo").returncode, 2)


class TestSet(WorkCase):
    def test_set_changes_manual_status(self):
        self.put("W001", "a", status="идея")
        result = self.work("set", "W001", "--status", "готово к работе")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.status_of("W001"), "готово к работе")
        self.clean()

    def test_set_rejects_unknown_status(self):
        self.put("W001", "a", status="идея")
        self.assertEqual(self.work("set", "W001", "--status", "почти").returncode, 2)
        self.assertEqual(self.status_of("W001"), "идея")

    def test_set_refuses_manual_status_for_feature_with_spec(self):
        self.spec()
        self.put("W001", "a", spec="specs/001-demo", status=None)
        self.assertEqual(self.work("set", "W001", "--status", "в работе").returncode, 2)

    def test_set_allows_cancelling_feature_with_spec(self):
        self.spec()
        self.put("W001", "a", spec="specs/001-demo", status=None)
        self.assertEqual(self.work("set", "W001", "--status", "отменено").returncode, 0)
        self.assertEqual(self.status_of("W001"), "отменено")

    def test_set_refuses_status_for_epic_except_cancel(self):
        self.epic()
        self.assertEqual(self.work("set", "W001", "--status", "готово").returncode, 2)
        self.assertEqual(self.work("set", "W001", "--status", "отменено").returncode, 0)

    def test_set_unknown_item(self):
        self.assertEqual(self.work("set", "W404", "--status", "идея").returncode, 2)


class TestResultAndResolves(WorkCase):
    REGISTRY = "docs/decisions/README.md"

    def drop_topic(self, topic):
        lines = self.repo.read(self.REGISTRY).split("\n")
        kept = [line for line in lines if not line.startswith(f"| {topic} |")]
        self.assertNotEqual(lines, kept, f"в реестре нет темы {topic}")
        self.repo.write(self.REGISTRY, "\n".join(kept))

    def research(self, ident="W001", **fields):
        self.put(ident, "r", type="исследование", kind=None, control=None, **fields)

    def test_result_is_not_allowed_for_a_feature(self):
        self.put("W001", "a", result="docs/README.md")
        self.detected("result допустим только")

    def test_done_research_needs_result(self):
        self.research(status="готово")
        self.detected("у готового исследования нужен result")

    def test_done_item_with_missing_result_file(self):
        self.research(status="готово", result="docs/decisions/9999-none.md")
        self.detected("файл result не найден")

    def test_done_item_with_existing_result_is_clean(self):
        self.research(status="готово", result="docs/decisions/0001-spec-kit-and-omc-roles.md")
        self.clean()

    def test_unfinished_item_may_name_a_future_result(self):
        self.research(status="в работе", result="docs/decisions/9999-future.md")
        self.clean()

    def test_task_may_be_done_without_result(self):
        self.put("W001", "a", type="задача", kind=None, control=None, status="готово")
        self.clean()

    def test_unknown_topic_while_unfinished(self):
        self.research(status="в работе", resolves="нет такой темы")
        self.detected("строки нет в реестре отложенных решений")

    def test_known_topic_while_unfinished_is_clean(self):
        self.research(status="в работе", resolves="Frontend")
        self.clean()

    def test_done_item_must_remove_its_topic_from_registry(self):
        self.research(status="готово", resolves="Frontend", result="docs/decisions/0001-spec-kit-and-omc-roles.md")
        self.detected("строка осталась в реестре отложенных решений")

    def test_done_item_with_topic_removed_is_clean(self):
        self.drop_topic("Frontend")
        self.research(status="готово", resolves="Frontend", result="docs/decisions/0001-spec-kit-and-omc-roles.md")
        self.clean()

    def test_several_topics_are_separated_by_semicolon(self):
        self.research(status="в работе", resolves="Frontend; Docker, деплой, окружения")
        self.clean()
        self.research(status="в работе", resolves="Frontend; нет такой")
        self.detected("нет такой")

    def test_cancelled_item_is_not_checked_against_registry(self):
        self.research(status="отменено", resolves="нет такой темы")
        self.clean()

    def test_derived_done_feature_must_remove_its_topic(self):
        self.spec(plan=True, tasks="- [x] T001 Тест\n")
        self.put("W001", "a", spec="specs/001-demo", status=None, resolves="Frontend")
        self.detected("строка осталась в реестре отложенных решений")

    def test_new_writes_result_and_resolves(self):
        result = self.work("new", "--type", "исследование", "--title", "R", "--slug", "r", "--resolves", "Frontend",
                           "--result", "docs/decisions/9999-future.md")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        text = self.repo.read("work/W001-r.md")
        self.assertIn("resolves: Frontend", text)
        self.assertIn("result: docs/decisions/9999-future.md", text)

    def test_new_rejects_unknown_topic(self):
        result = self.work("new", "--type", "исследование", "--title", "R", "--slug", "r", "--resolves", "выдумка")
        self.assertEqual(result.returncode, 2)

    def test_set_result_and_status_together(self):
        self.research(status="в работе")
        result = self.work("set", "W001", "--result", "docs/decisions/0001-spec-kit-and-omc-roles.md",
                           "--status", "готово")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.clean()

    def test_set_needs_status_or_result(self):
        self.research(status="в работе")
        self.assertEqual(self.work("set", "W001").returncode, 2)

    def test_show_prints_result_and_resolves(self):
        self.research(status="в работе", resolves="Frontend", result="docs/decisions/9999-future.md")
        out = self.work("show", "W001").stdout
        self.assertIn("решает: Frontend", out)
        self.assertIn("результат: docs/decisions/9999-future.md", out)


class TestCli(WorkCase):
    def test_unknown_command(self):
        self.assertEqual(self.work("nothing").returncode, 2)

    def test_no_command(self):
        self.assertEqual(self.work().returncode, 2)


if __name__ == "__main__":
    unittest.main()

"""Тесты tools/docs-lint.py: мутационные случаи на каждую проверку, режимы запуска и защита конституции.

Мутационный случай: ломаем одну вещь в копии репозитория и требуем, чтобы проверка это заметила.
Контрольный тест следит, чтобы у каждой функции check_* был хотя бы один такой случай.
"""
import json
import re
import unittest

from helpers import CONSTITUTION, ROOT, Repo, hook_payload, load_tool

COVERED = set()
FOOTER = re.compile(r"^(\*\*Версия\*\*: )(\d+)\.(\d+)\.(\d+)( \|)", re.M)
OVERRIDES = ".specify/templates/overrides"


def covers(name):
    """Отмечает, что тест покрывает проверку check_*."""
    def decorator(func):
        COVERED.add(name)
        return func
    return decorator


def bump(repo, part="minor"):
    text = repo.read(CONSTITUTION)
    match = FOOTER.search(text)
    major, minor, patch = (int(match.group(i)) for i in (2, 3, 4))
    if part == "major":
        major, minor, patch = major + 1, 0, 0
    elif part == "minor":
        minor, patch = minor + 1, 0
    elif part == "patch":
        patch += 1
    elif part == "down":
        minor = max(minor - 1, 0)
    repo.write(CONSTITUTION, FOOTER.sub(lambda m: f"{m.group(1)}{major}.{minor}.{patch}{m.group(5)}", text, 1))


class LintCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo = Repo()

    @classmethod
    def tearDownClass(cls):
        cls.repo.close()

    def setUp(self):
        self.repo.reset()

    def detected(self, expected, *flags):
        result = self.repo.lint(*flags)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(expected, result.stdout)
        return result

    def clean(self, *flags):
        result = self.repo.lint(*flags)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result


class TestBaseline(LintCase):
    def test_repository_is_consistent(self):
        self.clean()

    def test_every_check_has_a_mutation_case(self):
        module = load_tool("docs-lint")
        checks = {name for name in dir(module) if name.startswith("check_")}
        self.assertEqual(checks - COVERED, set(), "у проверки нет мутационного случая")
        self.assertEqual(COVERED - checks, set(), "мутационный случай ссылается на несуществующую проверку")


class TestDocumentChecks(LintCase):
    @covers("check_encoding")
    def test_non_utf8_file(self):
        self.repo.path("README.md").write_bytes(b"# bad \xff\xfe")
        self.detected("UTF-8")

    @covers("check_header")
    def test_missing_header_line(self):
        self.repo.write("AGENTS.md", re.sub(r"^> \*\*Отвечает за:\*\*.*\n", "", self.repo.read("AGENTS.md"), flags=re.M))
        self.detected("нет строки шапки")

    def test_header_lines_out_of_order(self):
        lines = self.repo.read("AGENTS.md").split("\n")
        index = next(i for i, l in enumerate(lines) if l.startswith("> **Отвечает за:**"))
        lines[index], lines[index + 1] = lines[index + 1], lines[index]
        self.repo.write("AGENTS.md", "\n".join(lines))
        self.detected("не в том порядке")

    @covers("check_freshness")
    def test_minor_bump_makes_files_stale(self):
        bump(self.repo, "minor")
        self.detected("старше версии конституции")

    def test_patch_bump_keeps_files_fresh(self):
        bump(self.repo, "patch")
        self.clean()

    def test_marker_newer_than_constitution(self):
        text = re.sub(r"(Проверено при версии конституции:\*\* )\d+\.\d+\.\d+", r"\g<1>9.9.9", self.repo.read("AGENTS.md"))
        self.repo.write("AGENTS.md", text)
        self.detected("новее версии конституции")

    def test_malformed_marker_is_reported(self):
        for bad in ("9.2.4.0", "abc", "2.4"):
            with self.subTest(marker=bad):
                self.repo.reset()
                text = re.sub(r"(Проверено при версии конституции:\*\* )\S+", r"\g<1>" + bad, self.repo.read("AGENTS.md"))
                self.repo.write("AGENTS.md", text)
                self.detected("не в формате X.Y.Z")

    @covers("check_links")
    def test_broken_link(self):
        self.repo.append("README.md", "\n[x](docs/nope.md)\n")
        self.detected("битая ссылка")

    def test_broken_anchor(self):
        self.repo.append("README.md", "\n[x](docs/README.md#нет-такого)\n")
        self.detected("нет якоря")

    @covers("check_registry_link")
    def test_undefined_topic_without_registry_link(self):
        text = re.sub(r"\(\.\./decisions/README\.md#отложенные-решения\)", "(../decisions/README.md)",
                      self.repo.read("docs/operations/README.md"))
        self.repo.write("docs/operations/README.md", text)
        self.detected("нет ссылки на реестр")

    @covers("check_forbidden")
    def test_forbidden_characters(self):
        for char in ("—", "–", "ё", "Ё", "«", "→", "…"):
            with self.subTest(char=hex(ord(char))):
                self.repo.reset()
                self.repo.append("README.md", f"\nтест {char} символ\n")
                self.detected("запрещенный символ")

    @covers("check_principle_refs")
    def test_reference_to_nonexistent_principle(self):
        self.repo.replace("AGENTS.md", "Принцип IX обязателен", "Принцип XII обязателен")
        self.detected("несуществующий принцип XII")

    def test_lowercase_reference_to_nonexistent_principle(self):
        self.repo.replace("AGENTS.md", "(принцип I)", "(принцип XIV)")
        self.detected("несуществующий принцип XIV")

    def test_renamed_principle_breaks_references(self):
        self.repo.replace(CONSTITUTION, "### IX. AI-нативная", "### X. AI-нативная")
        self.detected("несуществующий принцип IX")

    @covers("check_backtick_paths")
    def test_mentioned_path_does_not_exist(self):
        self.repo.append("README.md", "\nСкрипт `tools/nope.py` описан.\n")
        self.detected("несуществующий путь")

    @covers("check_map")
    def test_file_missing_from_map(self):
        self.repo.write("docs/architecture/extra.md", self.repo.read("docs/operations/README.md"))
        self.detected("нет в карте источников истины")

    def test_adr_files_do_not_need_their_own_map_row(self):
        text = self.repo.read("docs/decisions/0001-spec-kit-and-omc-roles.md").replace("ADR 0001", "ADR 0007")
        self.repo.write("docs/decisions/0007-extra.md", text)
        self.repo.append("docs/decisions/README.md", "\n[0007](0007-extra.md)\n")
        self.clean()

    @covers("check_adr_index")
    def test_adr_missing_from_index(self):
        text = self.repo.read("docs/decisions/0001-spec-kit-and-omc-roles.md")
        self.repo.write("docs/decisions/0007-extra.md", text)
        self.detected("ADR нет в указателе")


class TestHarnessWiring(LintCase):
    @covers("check_harness_documented")
    def test_new_tool_missing_from_diagram_and_guide(self):
        self.repo.write("tools/new-tool.py", "#!/usr/bin/env python3\n")
        result = self.detected("не показан на схеме связей")
        self.assertIn("не описан в справочнике", result.stdout)

    def test_new_git_hook_missing_from_diagram(self):
        self.repo.write(".githooks/pre-push", "x\n")
        self.detected("не показан на схеме связей")

    def test_new_project_skill_missing_from_diagram(self):
        self.repo.write(".claude/skills/new-skill/SKILL.md", "---\nname: new-skill\ndescription: x\n---\n# n\n")
        self.detected(".claude/skills/new-skill")

    @covers("check_wiring")
    def test_claude_md_lost_agents_import(self):
        self.repo.write("CLAUDE.md", self.repo.read("CLAUDE.md").replace("@AGENTS.md", ""))
        self.detected("@AGENTS.md")

    def test_settings_hook_points_to_missing_script(self):
        self.repo.write(".claude/settings.json", self.repo.read(".claude/settings.json").replace(
            "tools/docs-lint.py", "tools/docs-lint-typo.py"))
        self.detected("несуществующий файл")

    def test_spec_kit_hook_points_to_missing_skill(self):
        self.repo.replace(".specify/extensions.yml", "command: docs.sync", "command: docs.sinc")
        self.detected("не соответствует скиллу")

    def test_skill_name_differs_from_folder(self):
        self.repo.replace(".claude/skills/capture/SKILL.md", "name: capture", "name: capturex")
        self.detected("не совпадает с именем папки")

    def test_gitignore_lost_omc(self):
        self.repo.write(".gitignore", self.repo.read(".gitignore").replace(".omc/\n", ""))
        self.detected("нет строки .omc/")

    @covers("check_settings")
    def test_forbidden_plugin_enabled(self):
        self.repo.replace(".claude/settings.json", '"01-core@01tech-plugins": false',
                          '"01-core@01tech-plugins": true')
        self.detected("неразрешенный плагин")

    def test_enabled_plugins_not_an_object(self):
        settings = json.loads(self.repo.read(".claude/settings.json"))
        settings["enabledPlugins"] = []
        self.repo.write(".claude/settings.json", json.dumps(settings))
        self.detected("enabledPlugins должен быть объектом")

    @covers("check_test_coverage")
    def test_harness_file_without_tests(self):
        coverage = json.loads(self.repo.read("tests/harness/coverage.json"))
        del coverage["tools/harvest.py"]
        self.repo.write("tests/harness/coverage.json", json.dumps(coverage))
        self.detected("нет записи о тестах")

    def test_coverage_points_to_missing_test(self):
        coverage = json.loads(self.repo.read("tests/harness/coverage.json"))
        coverage["tools/harvest.py"] = ["tests/harness/test_nope.py"]
        self.repo.write("tests/harness/coverage.json", json.dumps(coverage))
        self.detected("не существует")

    def test_coverage_entry_for_deleted_file(self):
        coverage = json.loads(self.repo.read("tests/harness/coverage.json"))
        coverage["tools/gone.py"] = ["tests/harness/test_docs_lint.py"]
        self.repo.write("tests/harness/coverage.json", json.dumps(coverage))
        self.detected("запись о несуществующем файле харнеса")


class TestBricks(LintCase):
    FILE = "docs/harness/bricks.md"

    def mutate(self, old, new):
        self.repo.replace(self.FILE, old, new)

    @covers("check_bricks")
    def test_missing_catalog(self):
        self.repo.path(self.FILE).unlink()
        self.detected("нет каталога кирпичиков")

    def test_unknown_skill_in_executor(self):
        self.mutate("| `speckit-plan` |", "| `speckit-nope` |")
        self.detected("несуществующий скилл speckit-nope")

    def test_duplicate_brick_name(self):
        self.mutate("| `tasks` |", "| `plan` |")
        self.detected("повторяется имя кирпичика plan")

    def test_brick_name_must_be_identifier(self):
        self.mutate("| `tasks` |", "| Tasks |")
        self.detected("имя кирпичика должно быть в обратных кавычках")

    def test_unknown_control_level(self):
        self.mutate("| `docs-sync` | обновляет документацию и метки, запускает проверку | `docs-sync` | - | нет | автономный,",
                    "| `docs-sync` | обновляет документацию и метки, запускает проверку | `docs-sync` | - | нет | свободный,")
        self.detected("неизвестный уровень контроля свободный")

    def test_dialog_brick_cannot_be_autonomous(self):
        self.mutate("| да | контрольная точка, сопровождение | создан файл `spec.md` фичи",
                    "| да | автономный, контрольная точка | создан файл `spec.md` фичи")
        self.detected("диалоговый кирпичик specify не может быть автономным")

    def test_dialog_value_is_checked(self):
        self.mutate("| `speckit-clarify` | - | да |", "| `speckit-clarify` | - | возможно |")
        self.detected("диалог должен быть да или нет")

    def test_working_brick_needs_executor(self):
        self.mutate("| `speckit-tasks` |", "| - |")
        self.detected("кирпичик tasks работает, но исполнитель не указан")

    def test_brick_without_executor_status_cannot_name_one(self):
        self.mutate("| `find-pattern` | ищет образец реализации в каталоге образцов | - |",
                    "| `find-pattern` | ищет образец реализации в каталоге образцов | `capture` |")
        self.detected("кирпичик find-pattern без исполнителя, но исполнитель указан")

    def test_status_value_is_checked(self):
        self.mutate("| тесты проходят | работает |", "| тесты проходят | готово |")
        self.detected("неизвестный статус готово")

    def test_wrong_columns(self):
        self.mutate("| Статус |", "| Состояние |")
        self.detected("со столбцами каталога кирпичиков")

    def test_row_with_wrong_cell_count(self):
        self.mutate("| не определена | нет исполнителя |\n| `tasks`", "| не определена |\n| `tasks`")
        self.detected("число ячеек")

    def test_levels_table_is_required(self):
        self.mutate("| автономный | агент выполняет шаг сам, владелец смотрит итог |\n", "")
        self.detected("неизвестный уровень контроля автономный")


class TestWorkflows(LintCase):
    FILE = ".specify/workflows/feature-chain/workflow.yml"

    def mutate(self, old, new):
        self.repo.replace(self.FILE, old, new)

    @covers("check_workflows")
    def test_command_step_must_be_a_brick(self):
        self.mutate("  - id: plan\n", "  - id: planning\n")
        self.detected("шаг команды planning не найден в каталоге кирпичиков")

    def test_dialog_brick_cannot_be_in_the_chain(self):
        self.mutate("  - id: tasks\n    command: speckit.tasks", "  - id: clarify\n    command: speckit.tasks")
        self.detected("диалоговый кирпичик clarify нельзя ставить в цепочку")

    def test_brick_without_executor_cannot_be_in_the_chain(self):
        self.mutate("  - id: tasks\n    command: speckit.tasks", "  - id: find-pattern\n    command: speckit.tasks")
        self.detected("кирпичик find-pattern без исполнителя нельзя ставить в цепочку")

    def test_unknown_command_skill(self):
        self.mutate("command: speckit.plan", "command: speckit.nope")
        self.detected("команда speckit.nope не соответствует скиллу speckit-nope")

    def test_unknown_prompt_skill(self):
        self.mutate('prompt: "/docs-sync"', 'prompt: "/docs-nope"')
        self.detected("команда docs-nope не соответствует скиллу docs-nope")

    def test_command_step_needs_a_check_after_it(self):
        self.mutate("  - id: check-plan\n    type: shell\n    run: \"python3 tools/check-feature.py plan\"\n\n", "")
        self.detected("после шага команды plan нет проверки результата")

    def test_check_must_be_a_shell_step(self):
        self.mutate("  - id: check-plan\n    type: shell", "  - id: check-plan\n    type: gate")
        self.detected("после шага команды plan нет проверки результата")

    def test_shell_step_calls_missing_tool(self):
        self.mutate("tools/check-feature.py spec", "tools/check-nothing.py spec")
        self.detected("вызывает несуществующий файл tools/check-nothing.py")

    def test_unknown_control_level_in_condition(self):
        self.mutate("inputs.control == 'контрольная точка' }}\"\n    then:\n      - id: review-plan",
                    "inputs.control == 'свободный' }}\"\n    then:\n      - id: review-plan")
        self.detected("неизвестный уровень контроля свободный")

    def test_unknown_default_control_level(self):
        self.mutate('default: "контрольная точка"', 'default: "свободный"')
        self.detected("неизвестный уровень контроля свободный")

    def test_bundled_speckit_workflow_is_not_checked(self):
        self.clean()
        self.assertNotIn("speckit/workflow.yml", self.repo.lint().stdout)


class TestConstitutionAndTemplates(LintCase):
    @covers("check_constitution")
    def test_invalid_date(self):
        self.repo.replace(CONSTITUTION, "**Ратифицирована**: 2026-11-15", "**Ратифицирована**: 2026-13-45")
        self.detected("дата не в формате")

    def test_footer_missing(self):
        self.repo.write(CONSTITUTION, re.sub(r"^\*\*Версия\*\*.*$", "", self.repo.read(CONSTITUTION), flags=re.M))
        self.detected("нет строки версии")

    @covers("check_override")
    def test_tests_optional_in_tasks_override(self):
        self.repo.append(f"{OVERRIDES}/tasks-template.md", "\nTests are OPTIONAL\n")
        self.detected("необязательными")

    @covers("check_override_drift")
    def test_core_template_changed_upstream(self):
        self.repo.append(".specify/templates/tasks-template.md", "\n<!-- upstream change -->\n")
        self.detected("расходится с ядром")

    def test_rebuild_overrides_restores_consistency(self):
        self.repo.append(".specify/templates/tasks-template.md", "\n<!-- upstream change -->\n")
        result = self.repo.lint("--rebuild-overrides")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("upstream change", self.repo.read(f"{OVERRIDES}/tasks-template.md"))
        self.clean()

    def test_manual_override_edit_needs_patch_update(self):
        self.repo.append(f"{OVERRIDES}/tasks-template.md", "\nосознательная правка\n")
        self.detected("расходится")
        self.assertEqual(self.repo.lint("--update-patches").returncode, 0)
        self.clean()

    def test_rebuild_reports_conflict(self):
        self.repo.write(".specify/templates/tasks-template.md", "совсем другой шаблон\n")
        result = self.repo.lint("--rebuild-overrides")
        self.assertEqual(result.returncode, 2)

    def test_tasks_override_makes_tests_mandatory(self):
        text = (ROOT / OVERRIDES / "tasks-template.md").read_text(encoding="utf-8")
        self.assertIn("MANDATORY", text)
        self.assertNotRegex(text, r"(?i)OPTIONAL|if requested|tests requested")

    def test_plan_override_records_constitution_version(self):
        self.assertIn("Constitution version", (ROOT / OVERRIDES / "plan-template.md").read_text(encoding="utf-8"))

    @covers("check_plans")
    def test_plan_without_constitution_version(self):
        self.repo.write("specs/001-x/plan.md", "# Plan\n")
        self.detected("нет строки Constitution version")

    def test_plan_with_current_version_passes(self):
        version = ".".join(FOOTER.search(self.repo.read(CONSTITUTION)).group(2, 3, 4))
        self.repo.write("specs/001-x/plan.md", f"# Plan\n**Constitution version**: {version}\n")
        self.clean()

    def test_stale_plan_flagged_after_minor_bump(self):
        version = ".".join(FOOTER.search(self.repo.read(CONSTITUTION)).group(2, 3, 4))
        self.repo.write("specs/001-x/plan.md", f"# Plan\n**Constitution version**: {version}\n")
        bump(self.repo, "minor")
        for rel in load_tool("docs-lint").scope_files():
            if rel != CONSTITUTION:
                self.repo.lint("--stamp", rel)
        self.detected("более старой версии конституции")


class TestStagedGuard(LintCase):
    def stage_constitution(self):
        self.repo.git("add", CONSTITUTION)

    @covers("check_staged")
    def test_change_without_version_bump(self):
        self.repo.append(CONSTITUTION, "\nправка без версии\n")
        self.stage_constitution()
        self.detected("не больше прежней", "--staged")

    def test_version_downgrade(self):
        bump(self.repo, "down")
        self.stage_constitution()
        self.detected("не больше прежней", "--staged")

    def test_date_only_change(self):
        self.repo.replace(CONSTITUTION, "**Последняя поправка**: 2026-11-15", "**Последняя поправка**: 2026-11-16")
        self.stage_constitution()
        self.detected("не больше прежней", "--staged")

    def test_sync_report_left_in_constitution(self):
        bump(self.repo, "patch")
        self.repo.write(CONSTITUTION, "<!--\nSync Impact Report\n-->\n\n" + self.repo.read(CONSTITUTION))
        self.stage_constitution()
        self.detected("временный отчет", "--staged")

    def test_patch_bump_is_accepted(self):
        bump(self.repo, "patch")
        self.stage_constitution()
        self.clean("--staged")

    def test_phrase_in_body_is_not_a_report(self):
        bump(self.repo, "patch")
        self.stage_constitution()
        self.assertIn("Sync Impact Report", self.repo.read(CONSTITUTION))
        self.clean("--staged")


class TestModes(LintCase):
    def test_stamp_clears_only_the_stamped_file(self):
        bump(self.repo, "minor")
        self.assertEqual(self.repo.lint("--stamp", "AGENTS.md").returncode, 0)
        out = self.repo.lint().stdout
        self.assertNotIn("docs-lint: AGENTS.md:", out)
        self.assertIn("docs-lint: README.md:", out)

    def test_constitution_cannot_be_stamped(self):
        self.assertEqual(self.repo.lint("--stamp", CONSTITUTION).returncode, 2)

    def test_stamp_needs_files(self):
        self.assertEqual(self.repo.lint("--stamp").returncode, 2)

    def test_session_mode_enables_git_hook_and_reports(self):
        result = self.repo.lint("--session")
        self.assertEqual(result.returncode, 0)
        self.assertIn("Включил git-хук", result.stdout)
        self.assertEqual(self.repo.git("config", "core.hooksPath").stdout.strip(), ".githooks")

    def test_session_mode_is_silent_when_consistent(self):
        self.repo.git("config", "core.hooksPath", ".githooks")
        self.assertEqual(self.repo.lint("--session").stdout, "")

    def test_session_mode_shows_violations(self):
        self.repo.git("config", "core.hooksPath", ".githooks")
        self.repo.write("CLAUDE.md", self.repo.read("CLAUDE.md").replace("@AGENTS.md", ""))
        self.assertIn("Харнес несогласован", self.repo.lint("--session").stdout)

    def test_hook_mode_ignores_clean_and_unrelated_files(self):
        clean = hook_payload("PostToolUse", self.repo.dir)
        self.assertEqual(self.repo.lint("--hook", stdin=clean).returncode, 0)
        unrelated = clean.replace("README.md", "tools/docs-lint.py")
        self.assertEqual(self.repo.lint("--hook", stdin=unrelated).returncode, 0)

    def test_hook_mode_flags_the_edited_broken_file(self):
        self.repo.append("README.md", "\n[x](docs/nope.md)\n")
        result = self.repo.lint("--hook", stdin=hook_payload("PostToolUse", self.repo.dir))
        self.assertEqual(result.returncode, 2)
        self.assertIn("битая ссылка", result.stderr)

    def test_hook_mode_survives_bad_input(self):
        for stdin in ("not json", "[]", '{"tool_input": null}', ""):
            with self.subTest(stdin=stdin):
                self.assertEqual(self.repo.lint("--hook", stdin=stdin).returncode, 0)


if __name__ == "__main__":
    unittest.main()

"""Тесты tools/harness-eval.py: разбор вывода, проверки, матрица результатов и изоляция рабочей копии.

Настоящий claude не запускается: вместо него заглушка tests/harness/fixtures/fake_claude.py.
"""
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from helpers import FIXTURES, ROOT, load_tool

STUB = FIXTURES / "fake_claude.py"
os.chmod(STUB, 0o755)
os.environ["HARNESS_EVAL_CLAUDE"] = str(STUB)
ev = load_tool("harness-eval")


def stream(*items):
    return "\n".join(json.dumps(i, ensure_ascii=False) for i in items)


class StubCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="eval-test-"))
        self.old = (ev.CASES, ev.RESULTS, ev.CLAUDE)
        ev.CASES, ev.RESULTS = self.tmp / "cases", self.tmp / "results"
        ev.CASES.mkdir()

    def tearDown(self):
        ev.CASES, ev.RESULTS, ev.CLAUDE = self.old
        os.environ.pop("FAKE_CLAUDE_SPEC", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def stub(self, **spec):
        path = self.tmp / "spec.json"
        path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
        os.environ["FAKE_CLAUDE_SPEC"] = str(path)

    def add_case(self, name, **data):
        folder = ev.CASES / name
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "case.json").write_text(json.dumps({"prompt": "p", "graders": [], **data}, ensure_ascii=False),
                                          encoding="utf-8")


class TestParsing(unittest.TestCase):
    def test_parses_init_tools_text_result_cost_and_turns(self):
        out = stream(
            {"type": "system", "subtype": "init", "skills": ["capture"]},
            {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Skill", "input": {"skill": "capture"}}]}},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "привет"}]}},
            {"type": "result", "result": "итог", "is_error": False, "total_cost_usd": 0.5, "num_turns": 3},
        )
        run = ev.parse_stream(out)
        self.assertEqual(run["init"]["skills"], ["capture"])
        self.assertEqual(run["tools"], [{"name": "Skill", "input": {"skill": "capture"}}])
        self.assertEqual((run["text"], run["result"], run["cost"], run["turns"]), ("привет", "итог", 0.5, 3))

    def test_ignores_garbage_lines(self):
        run = ev.parse_stream("мусор\n[]\n123\n" + stream({"type": "result", "result": "ok"}))
        self.assertEqual(run["result"], "ok")


class TestGraders(unittest.TestCase):
    fake_run = {"init": {"skills": ["capture", "01-core:x"], "plugins": [{"source": "a@b"}]}, "result": "Нужна спецификация",
           "text": "", "tools": [{"name": "Skill", "input": {"skill": "capture"}}, {"name": "Read", "input": {}}]}
    before = {"a.md": "1", "b.md": "2"}

    def grade(self, spec, after):
        return ev.grade(spec, self.fake_run, self.before, after)[0]

    def test_file_graders(self):
        same = dict(self.before)
        self.assertTrue(self.grade({"type": "file_unchanged", "path": "a.md"}, same))
        self.assertFalse(self.grade({"type": "file_unchanged", "path": "a.md"}, {**same, "a.md": "9"}))
        self.assertTrue(self.grade({"type": "file_exists", "path": "a.md"}, same))
        self.assertFalse(self.grade({"type": "file_exists", "path": "zzz"}, same))

    def test_no_changes_matching_sees_created_modified_and_deleted(self):
        spec = {"type": "no_changes_matching", "glob": "*.go"}
        self.assertTrue(self.grade(spec, dict(self.before)))
        self.assertFalse(self.grade(spec, {**self.before, "src/main.go": "x"}))
        self.assertFalse(self.grade({"type": "no_changes_matching", "glob": "*.md"}, {"a.md": "1"}))
        self.assertFalse(self.grade({"type": "no_changes_matching", "glob": "b.md"}, {"a.md": "1", "b.md": "changed"}))

    def test_regex_in_result_is_case_insensitive(self):
        self.assertTrue(self.grade({"type": "regex_in_result", "pattern": "СПЕЦИФИКАЦ"}, {}))
        self.assertFalse(self.grade({"type": "regex_in_result", "pattern": "docker"}, {}))

    def test_tool_graders(self):
        self.assertTrue(self.grade({"type": "tool_used", "name": "Skill", "input_regex": "capture"}, {}))
        self.assertFalse(self.grade({"type": "tool_used", "name": "Skill", "input_regex": "audit"}, {}))
        self.assertTrue(self.grade({"type": "tool_not_used", "name": "Skill", "input_regex": "autopilot"}, {}))
        self.assertFalse(self.grade({"type": "tool_not_used", "name": "Read"}, {}))

    def test_init_lacks(self):
        self.assertFalse(self.grade({"type": "init_lacks", "field": "skills", "pattern": "^01-"}, {}))
        self.assertTrue(self.grade({"type": "init_lacks", "field": "skills", "pattern": "^qa-"}, {}))
        self.assertFalse(self.grade({"type": "init_lacks", "field": "plugins", "pattern": "a@b"}, {}))

    def test_init_lacks_fails_when_field_is_absent(self):
        ok, detail = ev.grade({"type": "init_lacks", "field": "nope", "pattern": "x"}, self.fake_run, {}, {})
        self.assertFalse(ok)
        self.assertIn("нет поля", detail)

    def test_unknown_grader_fails(self):
        self.assertFalse(self.grade({"type": "magic"}, {}))


class TestSnapshot(StubCase):
    def test_gitignored_files_are_not_counted(self):
        workspace = ev.make_workspace()
        try:
            before = ev.snapshot(workspace)
            (workspace / ".omc").mkdir()
            (workspace / ".omc" / "state.json").write_text("{}", encoding="utf-8")
            (workspace / "__pycache__").mkdir()
            (workspace / "__pycache__" / "x.pyc").write_text("x", encoding="utf-8")
            self.assertEqual(ev.snapshot(workspace), before)
            (workspace / "new.go").write_text("package x", encoding="utf-8")
            self.assertIn("new.go", ev.snapshot(workspace))
        finally:
            shutil.rmtree(workspace, ignore_errors=True)

    def test_deleted_tracked_file_counts_as_a_change(self):
        workspace = ev.make_workspace()
        try:
            before = ev.snapshot(workspace)
            (workspace / "AGENTS.md").unlink()
            self.assertNotIn("AGENTS.md", ev.snapshot(workspace))
            self.assertIn("AGENTS.md", before)
        finally:
            shutil.rmtree(workspace, ignore_errors=True)

    def test_snapshot_without_git_falls_back_to_directory_walk(self):
        (self.tmp / "plain").mkdir()
        (self.tmp / "plain" / "a.txt").write_text("a", encoding="utf-8")
        self.assertEqual(list(ev.snapshot(self.tmp / "plain")), ["a.txt"])


class TestWorkspaceAndSetup(StubCase):
    def test_workspace_is_an_isolated_copy_without_git_history_of_source(self):
        workspace = ev.make_workspace()
        try:
            self.assertTrue((workspace / "AGENTS.md").exists())
            self.assertTrue((workspace / ".git").is_dir())
            (workspace / "AGENTS.md").write_text("changed", encoding="utf-8")
            self.assertNotEqual((ROOT / "AGENTS.md").read_text(encoding="utf-8"), "changed")
        finally:
            shutil.rmtree(workspace, ignore_errors=True)

    def test_replace_setup_and_missing_text(self):
        workspace = ev.make_workspace()
        try:
            ev.apply_setup(workspace, [{"type": "replace", "path": "AGENTS.md", "old": "# AGENTS.md", "new": "# X"}])
            self.assertTrue((workspace / "AGENTS.md").read_text(encoding="utf-8").startswith("# X"))
            with self.assertRaises(ValueError):
                ev.apply_setup(workspace, [{"type": "replace", "path": "AGENTS.md", "old": "нет такого", "new": "y"}])
            with self.assertRaises(ValueError):
                ev.apply_setup(workspace, [{"type": "magic"}])
        finally:
            shutil.rmtree(workspace, ignore_errors=True)

    def test_bump_constitution_parts(self):
        workspace = ev.make_workspace()
        try:
            path = workspace / ".specify/memory/constitution.md"
            before = ev.FOOTER.search(path.read_text(encoding="utf-8")).group(2, 3, 4)
            ev.apply_setup(workspace, [{"type": "bump_constitution", "part": "minor"}])
            after = ev.FOOTER.search(path.read_text(encoding="utf-8")).group(2, 3, 4)
            self.assertEqual((int(after[0]), int(after[1]), int(after[2])), (int(before[0]), int(before[1]) + 1, 0))
        finally:
            shutil.rmtree(workspace, ignore_errors=True)


class TestRunCase(StubCase):
    case = {"name": "c", "prompt": "p", "graders": [
        {"type": "no_changes_matching", "glob": "*.go"}, {"type": "regex_in_result", "pattern": "спецификац"}]}

    def test_passing_run(self):
        self.stub(result="Нужна спецификация", cost=0.2, turns=2)
        outcome = ev.run_case(self.case, "m", 1.0, 60)
        self.assertTrue(outcome["ok"], outcome)
        self.assertEqual((outcome["cost"], outcome["turns"]), (0.2, 2))

    def test_failing_run_reports_which_grader_failed(self):
        self.stub(result="готово", write_files={"main.go": "package main"})
        outcome = ev.run_case(self.case, "m", 1.0, 60)
        self.assertFalse(outcome["ok"])
        self.assertEqual([g["ok"] for g in outcome["graders"]], [False, False])

    def test_agent_changes_never_reach_the_real_repository(self):
        self.stub(result="x", write_files={"main.go": "package main", "AGENTS.md": "испорчено"})
        ev.run_case(self.case, "m", 1.0, 60)
        self.assertFalse((ROOT / "main.go").exists())
        self.assertNotEqual((ROOT / "AGENTS.md").read_text(encoding="utf-8"), "испорчено")

    def test_process_error_is_a_failure_with_reason(self):
        self.stub(exit_code=1, stderr="Budget limit reached")
        outcome = ev.run_case(self.case, "m", 1.0, 60)
        self.assertFalse(outcome["ok"])
        self.assertIn("Budget limit", outcome["error"])

    def test_setup_error_is_reported_not_raised(self):
        self.stub(result="x")
        broken = {**self.case, "setup": [{"type": "replace", "path": "AGENTS.md", "old": "нет такого", "new": "y"}]}
        self.assertIn("нет текста", ev.run_case(broken, "m", 1.0, 60)["error"])

    def test_setup_step_with_missing_field_is_reported_not_raised(self):
        self.stub(result="x")
        broken = {**self.case, "setup": [{"type": "write", "path": "x.md", "content": "y"}]}
        self.assertIn("нет поля text", ev.run_case(broken, "m", 1.0, 60)["error"])

    def test_missing_claude_binary_is_reported(self):
        ev.CLAUDE = "/nonexistent/claude"
        outcome = ev.run_case(self.case, "m", 1.0, 60)
        self.assertFalse(outcome["ok"])
        self.assertIn("не найдена", outcome["error"])


class TestCasesAndSuites(StubCase):
    def test_slow_cases_need_a_flag_or_an_explicit_name(self):
        self.add_case("fast")
        self.add_case("slow", slow=True)
        self.assertEqual([c["name"] for c in ev.load_cases([], False)], ["fast"])
        self.assertEqual([c["name"] for c in ev.load_cases([], True)], ["fast", "slow"])
        self.assertEqual([c["name"] for c in ev.load_cases(["slow"], False)], ["slow"])

    def test_shipped_cases_are_well_formed(self):
        ev.CASES = ROOT / "evals" / "cases"
        cases = ev.load_cases([], True)
        self.assertGreaterEqual(len(cases), 6)
        allowed = {"file_unchanged", "file_exists", "no_changes_matching", "regex_in_result", "tool_used",
                   "tool_not_used", "init_lacks"}
        for case in cases:
            with self.subTest(case=case["name"]):
                self.assertTrue(case["prompt"])
                self.assertTrue(case["graders"])
                self.assertTrue({g["type"] for g in case["graders"]} <= allowed)
                for step in case.get("setup", []):
                    self.assertIn(step.get("type"), ev.SETUP_FIELDS)
                    self.assertLessEqual(set(ev.SETUP_FIELDS[step["type"]]), set(step))

    def test_cold_start_suite_is_built_from_questions(self):
        cases = ev.cold_start_cases()
        rows = (ROOT / ".claude/skills/cold-start/questions.md").read_text(encoding="utf-8").count("\n| ") - 1
        self.assertEqual(len(cases), rows)
        first = cases[0]
        self.assertIn("Источник", first["prompt"])
        self.assertTrue(any(g["type"] == "regex_in_result" for g in first["graders"]))

    def test_case_filter_applies_to_the_cold_start_suite(self):
        self.stub(result="x")
        code = ev.main(["--suite", "cold-start", "--case", "cold-start-03", "--models", "m", "--runs", "1", "--list"])
        self.assertEqual(code, 0)
        self.assertEqual([c["name"] for c in ev.cold_start_cases() if c["name"] == "cold-start-03"], ["cold-start-03"])

    def test_failed_run_keeps_a_fragment_of_the_answer(self):
        self.stub(result="Вот мой ответ про Docker")
        case = {"name": "c", "prompt": "p", "graders": [{"type": "regex_in_result", "pattern": "нет такого"}]}
        outcome = ev.run_case(case, "m", 1.0, 60)
        self.assertFalse(outcome["ok"])
        self.assertIn("Docker", outcome["answer"])

    def test_cold_start_accepts_any_of_several_owners(self):
        questions = self.tmp / "q.md"
        questions.write_text("| № | В | О |\n|---|---|---|\n| 1 | a | `docs/README.md` или `docs/development/README.md` |\n",
                             encoding="utf-8")
        pattern = ev.cold_start_cases(questions)[0]["graders"][0]["pattern"]
        import re as _re
        self.assertTrue(_re.search(pattern, "см. docs/README.md"))
        self.assertTrue(_re.search(pattern, "см. docs/development/README.md"))
        self.assertFalse(_re.search(pattern, "см. docs/operations/README.md"))

    def test_cold_start_pattern_accepts_basename_only_when_unique(self):
        questions = self.tmp / "q.md"
        questions.write_text("| № | В | О |\n|---|---|---|\n| 1 | a | `docs/README.md` |\n| 2 | b | `README.md` |\n"
                             "| 3 | c | `AGENTS.md` |\n", encoding="utf-8")
        patterns = {c["name"]: c["graders"][0]["pattern"] for c in ev.cold_start_cases(questions)}
        self.assertNotIn("|README\\.md", patterns["cold-start-01"])
        self.assertIn("|AGENTS\\.md", patterns["cold-start-03"])


class TestSummaryAndMain(StubCase):
    def result(self, case, model, ok):
        return {"case": case, "model": model, "ok": ok, "error": None, "graders": [], "cost": 0.1, "turns": 1}

    def test_matrix_verdicts(self):
        results = [self.result("a", "sonnet", True), self.result("a", "haiku", True),
                   self.result("b", "sonnet", True), self.result("b", "haiku", False),
                   self.result("c", "sonnet", False), self.result("c", "haiku", True)]
        rows, failed = ev.summarize(results, ["sonnet", "haiku"], 0.67)
        verdicts = {row[0]: row[2] for row in rows}
        self.assertEqual(verdicts["a"], "надежно")
        self.assertEqual(verdicts["b"], "зависит от силы модели")
        self.assertIn("дефект", verdicts["c"])
        self.assertTrue(failed)

    def test_canary_failure_does_not_fail_the_run(self):
        results = [self.result("b", "sonnet", True), self.result("b", "haiku", False)]
        self.assertFalse(ev.summarize(results, ["sonnet", "haiku"], 0.67)[1])

    def test_threshold_is_respected(self):
        results = [self.result("a", "m", True), self.result("a", "m", False), self.result("a", "m", False)]
        self.assertTrue(ev.summarize(results, ["m"], 0.67)[1])
        self.assertFalse(ev.summarize(results, ["m"], 0.3)[1])

    def test_main_exit_codes_and_results_file(self):
        self.add_case("guard", graders=[{"type": "no_changes_matching", "glob": "*.go"}])
        self.stub(result="ok", by_model={"weak": {"write_files": {"x.go": "package x"}}})
        code = ev.main(["--models", "strong,weak", "--runs", "1", "--parallel", "1"])
        self.assertEqual(code, 0)
        data = json.loads((ev.RESULTS / "last.json").read_text(encoding="utf-8"))
        self.assertEqual({(r["model"], r["ok"]) for r in data}, {("strong", True), ("weak", False)})
        self.stub(result="ok", by_model={"strong": {"write_files": {"x.go": "package x"}}})
        self.assertEqual(ev.main(["--models", "strong,weak", "--runs", "1", "--parallel", "1"]), 1)

    def test_main_errors(self):
        self.assertEqual(ev.main(["--case", "nope"]), 2)
        self.add_case("one")
        ev.CLAUDE = "definitely-not-installed-claude"
        self.assertEqual(ev.main(["--runs", "1"]), 2)

    def test_list_mode_does_not_run_anything(self):
        self.add_case("one", description="описание")
        self.assertEqual(ev.main(["--list"]), 0)


if __name__ == "__main__":
    unittest.main()

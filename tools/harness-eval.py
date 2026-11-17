#!/usr/bin/env python3
"""Сценарии поведения агента: запускает Claude Code без интерфейса в чистой копии репозитория и проверяет результат.

Каждый сценарий лежит в evals/cases/<имя>/case.json. Копия репозитория временная, настоящие файлы не меняются.
Первая модель из списка рабочая и определяет код возврата, остальные только показывают запас прочности.

Запуск:
    python3 tools/harness-eval.py --list
    python3 tools/harness-eval.py                       все быстрые сценарии, 3 запуска на каждой модели
    python3 tools/harness-eval.py --case no-code-without-spec --runs 1 --models haiku
    python3 tools/harness-eval.py --slow                добавить медленные сценарии
    python3 tools/harness-eval.py --suite cold-start    вопросы из .claude/skills/cold-start/questions.md

Коды возврата: 0 все сценарии рабочей модели пройдены, 1 есть провалы, 2 ошибка запуска.
Переменная HARNESS_EVAL_CLAUDE подменяет команду claude, это нужно тестам раннера.
"""
import argparse
import concurrent.futures
import fnmatch
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CASES = ROOT / "evals" / "cases"
RESULTS = ROOT / "evals" / "results"
QUESTIONS = ROOT / ".claude" / "skills" / "cold-start" / "questions.md"
CLAUDE = os.environ.get("HARNESS_EVAL_CLAUDE", "claude")
ALLOWED_TOOLS = ["Read", "Grep", "Glob", "Skill", "Agent", "TodoWrite", "Bash(git *)", "Bash(python3 tools/*)",
                 "Bash(ls *)", "Bash(cat *)", "Bash(grep *)", "Bash(wc *)"]
FOOTER = re.compile(r"^(\*\*Версия\*\*: )(\d+)\.(\d+)\.(\d+)( \|)", re.M)


# ---------- рабочая копия ----------

def snapshot(directory):
    """Хеши файлов копии. Файлы из .gitignore, например состояние OMC, не учитываются."""
    directory = Path(directory)
    if (directory / ".git").exists():
        listed = subprocess.run(["git", "ls-files", "-co", "--exclude-standard"], cwd=directory,
                                capture_output=True, text=True).stdout.split("\n")
        files = [directory / rel for rel in listed if rel]
    else:
        files = [p for p in directory.rglob("*") if ".git" not in p.relative_to(directory).parts]
    return {str(p.relative_to(directory)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files if p.is_file()}


def make_workspace(source=ROOT):
    """Копия рабочего состояния репозитория в временной папке с чистым git."""
    workspace = Path(tempfile.mkdtemp(prefix="harness-eval-"))
    files = subprocess.run(["git", "ls-files", "-co", "--exclude-standard"], cwd=source,
                           capture_output=True, text=True).stdout.split("\n")
    for rel in filter(None, files):
        src = Path(source) / rel
        if src.is_file():
            (workspace / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, workspace / rel)
    env = {**os.environ, "GIT_AUTHOR_NAME": "eval", "GIT_AUTHOR_EMAIL": "e@e", "GIT_COMMITTER_NAME": "eval",
           "GIT_COMMITTER_EMAIL": "e@e"}
    subprocess.run(["git", "init", "-q"], cwd=workspace, env=env)
    subprocess.run(["git", "add", "-A"], cwd=workspace, env=env, capture_output=True)
    subprocess.run(["git", "commit", "-q", "-m", "base", "--no-verify"], cwd=workspace, env=env, capture_output=True)
    return workspace


SETUP_FIELDS = {"replace": ("path", "old", "new"), "write": ("path", "text"), "bump_constitution": ()}


def apply_setup(workspace, steps):
    for step in steps or []:
        kind = step.get("type")
        for field in SETUP_FIELDS.get(kind, ()):
            if field not in step:
                raise ValueError(f"в шаге подготовки {kind} нет поля {field}")
        if kind == "replace":
            path = workspace / step["path"]
            text = path.read_text(encoding="utf-8")
            if step["old"] not in text:
                raise ValueError(f"в {step['path']} нет текста для замены: {step['old'][:40]}")
            path.write_text(text.replace(step["old"], step["new"], 1), encoding="utf-8")
        elif kind == "write":
            path = workspace / step["path"]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(step["text"], encoding="utf-8")
        elif kind == "bump_constitution":
            path = workspace / ".specify" / "memory" / "constitution.md"
            text = path.read_text(encoding="utf-8")
            match = FOOTER.search(text)
            if not match:
                raise ValueError("в конституции нет строки версии")
            major, minor, patch = (int(match.group(i)) for i in (2, 3, 4))
            major, minor, patch = (major + 1, 0, 0) if step.get("part") == "major" else (
                (major, minor, patch + 1) if step.get("part") == "patch" else (major, minor + 1, 0))
            path.write_text(FOOTER.sub(lambda m: f"{m.group(1)}{major}.{minor}.{patch}{m.group(5)}", text, 1),
                            encoding="utf-8")
        else:
            raise ValueError(f"неизвестный шаг подготовки: {kind}")


def clean_state(workspace):
    """Удаляет следы запуска в ~/.claude/projects для временной папки."""
    for variant in {str(workspace), str(workspace.resolve())}:
        encoded = re.sub(r"[^A-Za-z0-9]", "-", variant)
        shutil.rmtree(Path.home() / ".claude" / "projects" / encoded, ignore_errors=True)


# ---------- запуск и разбор ----------

def run_claude(workspace, prompt, model, budget, timeout):
    command = [CLAUDE, "-p", prompt, "--model", model, "--output-format", "stream-json", "--verbose",
               "--no-session-persistence", "--permission-mode", "acceptEdits", "--max-budget-usd", str(budget),
               "--allowedTools", *ALLOWED_TOOLS]
    env = {**os.environ, "CAPTURE_REMIND_EVERY": "0"}
    try:
        done = subprocess.run(command, cwd=workspace, env=env, stdin=subprocess.DEVNULL, capture_output=True,
                              text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"error": f"таймаут {timeout} с"}
    except FileNotFoundError:
        return {"error": f"команда {CLAUDE} не найдена"}
    parsed = parse_stream(done.stdout)
    if done.returncode != 0 and not parsed["result"]:
        parsed["error"] = f"код возврата {done.returncode}: {done.stderr.strip()[:200]}"
    return parsed


def parse_stream(stdout):
    run = {"init": {}, "tools": [], "text": "", "result": "", "is_error": False, "cost": 0.0, "turns": 0, "error": None}
    chunks = []
    for line in stdout.splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if not isinstance(item, dict):
            continue
        kind = item.get("type")
        if kind == "system" and item.get("subtype") == "init":
            run["init"] = item
        elif kind == "assistant":
            content = item.get("message", {}).get("content", [])
            for block in content if isinstance(content, list) else []:
                if block.get("type") == "tool_use":
                    run["tools"].append({"name": block.get("name"), "input": block.get("input", {})})
                elif block.get("type") == "text":
                    chunks.append(block.get("text", ""))
        elif kind == "result":
            run["result"] = str(item.get("result") or "")
            run["is_error"] = bool(item.get("is_error"))
            run["cost"] = float(item.get("total_cost_usd") or 0.0)
            run["turns"] = int(item.get("num_turns") or 0)
    run["text"] = "\n".join(chunks)
    return run


# ---------- проверки ----------

def init_items(init, field):
    items = init.get(field)
    if not isinstance(items, list):
        return None
    return [json.dumps(i, ensure_ascii=False) if isinstance(i, dict) else str(i) for i in items]


def grade(spec, run, before, after):
    """Возвращает (прошла ли проверка, пояснение)."""
    kind = spec.get("type")
    changed = sorted(p for p in set(before) | set(after) if before.get(p) != after.get(p))
    if kind == "file_unchanged":
        ok = before.get(spec["path"]) == after.get(spec["path"])
        return ok, f"файл {spec['path']} {'не изменен' if ok else 'изменен'}"
    if kind == "file_exists":
        ok = spec["path"] in after
        return ok, f"файл {spec['path']} {'есть' if ok else 'отсутствует'}"
    if kind == "no_changes_matching":
        hits = [p for p in changed if fnmatch.fnmatch(p, spec["glob"])]
        return not hits, "лишних изменений нет" if not hits else f"изменены или созданы: {hits[:3]}"
    if kind == "regex_in_result":
        found = re.search(spec["pattern"], run["result"] + "\n" + run["text"], re.I | re.S)
        return bool(found), f"шаблон {spec['pattern']!r} {'найден' if found else 'не найден'} в ответе"
    if kind in ("tool_used", "tool_not_used"):
        pattern = spec.get("input_regex")
        hits = [t for t in run["tools"] if t["name"] == spec["name"]
                and (not pattern or re.search(pattern, json.dumps(t["input"], ensure_ascii=False), re.I))]
        used = bool(hits)
        ok = used if kind == "tool_used" else not used
        return ok, f"инструмент {spec['name']} {'вызван' if used else 'не вызван'}"
    if kind == "init_lacks":
        items = init_items(run["init"], spec["field"])
        if items is None:
            return False, f"в событии инициализации нет поля {spec['field']}"
        hits = [i for i in items if re.search(spec["pattern"], i)]
        return not hits, "запрещенных элементов нет" if not hits else f"найдено: {hits[:3]}"
    return False, f"неизвестный тип проверки: {kind}"


def run_case(case, model, budget, timeout):
    workspace = make_workspace()
    try:
        apply_setup(workspace, case.get("setup"))
        before = snapshot(workspace)
        run = run_claude(workspace, case["prompt"], model, budget, timeout)
        if run.get("error"):
            return {"case": case["name"], "model": model, "ok": False, "error": run["error"], "graders": [],
                    "cost": 0.0, "turns": 0}
        after = snapshot(workspace)
        graders = []
        for spec in case["graders"]:
            ok, detail = grade(spec, run, before, after)
            graders.append({"type": spec.get("type"), "ok": ok, "detail": detail})
        return {"case": case["name"], "model": model, "ok": all(g["ok"] for g in graders), "error": None,
                "graders": graders, "cost": run["cost"], "turns": run["turns"],
                "answer": (run["result"] or run["text"])[:400]}
    except (ValueError, OSError) as exc:
        return {"case": case["name"], "model": model, "ok": False, "error": str(exc), "graders": [], "cost": 0.0,
                "turns": 0}
    finally:
        clean_state(workspace)
        shutil.rmtree(workspace, ignore_errors=True)


# ---------- набор сценариев ----------

def load_cases(names, include_slow):
    cases = []
    for path in sorted(CASES.glob("*/case.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        data["name"] = path.parent.name
        if names and data["name"] not in names:
            continue
        if data.get("slow") and not include_slow and not names:
            continue
        cases.append(data)
    return cases


def cold_start_cases(questions=QUESTIONS):
    rows = []
    for number, question, cell in re.findall(r"^\| (\d+) \| (.*?) \| (.+?) \|$", questions.read_text(encoding="utf-8"), re.M):
        owners = re.findall(r"`([^`]+)`", cell)
        if owners:
            rows.append((number, question, owners))
    basenames = [Path(owner).name for _, _, owners in rows for owner in owners]
    cases = []
    for number, question, owners in rows:
        options = []
        for owner in owners:
            options.append(re.escape(owner))
            if basenames.count(Path(owner).name) == 1:
                options.append(re.escape(Path(owner).name))
        pattern = "|".join(options)
        cases.append({
            "name": f"cold-start-{int(number):02d}",
            "prompt": ("Отвечай только по файлам этого репозитория: начни с AGENTS.md и иди по ссылкам. "
                       f"Вопрос: {question}\nВ конце напиши строку 'Источник: <путь файла, где нашел ответ>'."),
            "graders": [{"type": "regex_in_result", "pattern": pattern}, {"type": "no_changes_matching", "glob": "*"}],
        })
    return cases


# ---------- отчет ----------

def summarize(results, models, threshold):
    table = {}
    for item in results:
        cell = table.setdefault(item["case"], {}).setdefault(item["model"], [0, 0])
        cell[1] += 1
        cell[0] += 1 if item["ok"] else 0
    gate = models[0]
    rows, failed = [], False
    for case in sorted(table):
        cells = table[case]
        def passed(model):
            ok, total = cells.get(model, [0, 0])
            return total > 0 and ok / total >= threshold
        verdict = "надежно"
        if not passed(gate):
            verdict, failed = "дефект: провал на рабочей модели", True
        elif any(not passed(m) for m in models[1:]):
            verdict = "зависит от силы модели"
        rows.append((case, {m: cells.get(m, [0, 0]) for m in models}, verdict))
    return rows, failed


def print_report(rows, models, results):
    width = max([len(r[0]) for r in rows] + [8])
    print(f"{'сценарий'.ljust(width)}  " + "  ".join(m.ljust(8) for m in models) + "  вывод")
    for case, cells, verdict in rows:
        print(f"{case.ljust(width)}  " + "  ".join(f"{cells[m][0]}/{cells[m][1]}".ljust(8) for m in models) + f"  {verdict}")
    print(f"\nзапусков: {len(results)}, стоимость: ${sum(r['cost'] for r in results):.2f}")
    for item in results:
        if item["ok"]:
            continue
        reason = item["error"] or "; ".join(g["detail"] for g in item["graders"] if not g["ok"])
        print(f"  провал {item['case']} [{item['model']}]: {reason}")
        if item.get("answer"):
            print(f"    ответ: {item['answer'][:200]!r}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--case", action="append", default=[], help="имя сценария, можно несколько раз")
    parser.add_argument("--suite", choices=["cold-start"], help="набор, который строится автоматически")
    parser.add_argument("--models", default="sonnet,haiku", help="модели через запятую, первая рабочая")
    parser.add_argument("--runs", type=int, default=3, help="запусков на сценарий и модель")
    parser.add_argument("--parallel", type=int, default=3)
    parser.add_argument("--threshold", type=float, default=0.67, help="доля пройденных запусков")
    parser.add_argument("--budget", type=float, default=1.0, help="лимит стоимости одного запуска в долларах")
    parser.add_argument("--timeout", type=int, default=600, help="лимит времени одного запуска в секундах")
    parser.add_argument("--slow", action="store_true", help="включить медленные сценарии")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args(argv)

    if args.suite == "cold-start":
        cases = [c for c in cold_start_cases() if not args.case or c["name"] in args.case]
    else:
        cases = load_cases(args.case, args.slow)
    if args.list:
        for case in cases:
            print(f"{case['name']}{' (медленный)' if case.get('slow') else ''}: {case.get('description', '')}")
        return 0
    if not cases:
        print("harness-eval: нет подходящих сценариев", file=sys.stderr)
        return 2
    if shutil.which(CLAUDE) is None and not Path(CLAUDE).exists():
        print(f"harness-eval: команда {CLAUDE} не найдена", file=sys.stderr)
        return 2
    models = [m.strip() for m in args.models.split(",") if m.strip()]
    tasks = [(case, model) for case in cases for model in models for _ in range(case.get("runs", args.runs))]
    print(f"сценариев: {len(cases)}, моделей: {len(models)}, запусков: {len(tasks)}", flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.parallel)) as pool:
        results = list(pool.map(lambda t: run_case(t[0], t[1], args.budget, args.timeout), tasks))
    rows, failed = summarize(results, models, args.threshold)
    print_report(rows, models, results)
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "last.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

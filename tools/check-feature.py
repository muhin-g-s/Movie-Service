#!/usr/bin/env python3
"""Детерминированная проверка артефактов фичи между шагами цепочки воркфлоу. Нужен только Python 3.

Запуск:
    python3 tools/check-feature.py spec    есть spec.md, он не пуст, маркеров уточнения не осталось
    python3 tools/check-feature.py plan    есть plan.md, в нем актуальная Constitution version
    python3 tools/check-feature.py autonomy в плане нет затронутых горячих зон, иначе автономный режим запрещен
    python3 tools/check-feature.py tasks   есть tasks.md и в нем есть задачи
    python3 tools/check-feature.py done    в tasks.md не осталось невыполненных задач

Каталог фичи берется из .specify/feature.json, ключ feature_directory.
Код возврата: 0 проверка пройдена, 1 нарушение, 2 нет каталога фичи или неизвестный этап.
"""
import importlib.util
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TASK_LINE = re.compile(r"^\s*- \[( |x|X)\] (T\d+)\b", re.M)
OPEN_MARKER = re.compile(r"\[NEEDS CLARIFICATION", re.I)


def lint_module():
    spec = importlib.util.spec_from_file_location("docs_lint_for_check", ROOT / "tools" / "docs-lint.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def feature_directory():
    try:
        value = json.loads((ROOT / ".specify" / "feature.json").read_text(encoding="utf-8"))["feature_directory"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    path = Path(value)
    path = path if path.is_absolute() else ROOT / path
    return path if path.is_dir() else None


def fail(message):
    print(f"check-feature: {message}", file=sys.stderr)
    return 1


def read_artifact(folder, name):
    path = folder / name
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8", errors="replace")


def check_spec(folder):
    text = read_artifact(folder, "spec.md")
    if text is None:
        return fail("нет файла spec.md")
    if not text.strip():
        return fail("spec.md пуст")
    if OPEN_MARKER.search(text):
        return fail("в spec.md остались маркеры уточнения, закройте их через /speckit-clarify")
    return 0


def check_plan(folder):
    text = read_artifact(folder, "plan.md")
    if text is None:
        return fail("нет файла plan.md")
    lint = lint_module()
    match = lint.PLAN_RE.search(text)
    if not match:
        return fail("в plan.md нет строки Constitution version")
    current = lint.current_version()
    marker = tuple(int(x) for x in match.groups())
    if current and marker[:2] < current[:2]:
        return fail(f"план составлен по версии конституции {'.'.join(map(str, marker))}, "
                    f"это старше версии {'.'.join(map(str, current))}")
    return 0


def check_autonomy(folder):
    text = read_artifact(folder, "plan.md")
    if text is None:
        return fail("нет файла plan.md")
    match = re.search(r"\*\*Hot zones touched\*\*:[ \t]*(.*)", text)
    if not match:
        return fail("в plan.md нет поля Hot zones touched")
    value = match.group(1).strip()
    if not value or value.startswith("["):
        return fail("поле Hot zones touched не заполнено")
    if re.match(r"нет\b", value):
        return 0
    return fail(f"задача затрагивает горячие зоны, автономный режим запрещен: {value}")


def check_tasks(folder):
    text = read_artifact(folder, "tasks.md")
    if text is None:
        return fail("нет файла tasks.md")
    if not TASK_LINE.search(text):
        return fail("в tasks.md нет задач в формате '- [ ] T001'")
    return 0


def check_done(folder):
    text = read_artifact(folder, "tasks.md")
    if text is None:
        return fail("нет файла tasks.md")
    opened = [task for mark, task in TASK_LINE.findall(text) if mark == " "]
    if opened:
        return fail("не выполнены задачи: " + ", ".join(opened))
    return 0


STAGES = {"spec": check_spec, "plan": check_plan, "autonomy": check_autonomy, "tasks": check_tasks, "done": check_done}


def main():
    args = sys.argv[1:]
    stage = args[0] if args else ""
    folder = feature_directory()
    if folder is None:
        print("check-feature: не найден каталог фичи, проверьте .specify/feature.json", file=sys.stderr)
        return 2
    if stage not in STAGES:
        print(f"check-feature: неизвестный этап {stage!r}, допустимы: {', '.join(STAGES)}", file=sys.stderr)
        return 2
    return STAGES[stage](folder)


if __name__ == "__main__":
    sys.exit(main())

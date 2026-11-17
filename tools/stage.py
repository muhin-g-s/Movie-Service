#!/usr/bin/env python3
"""Этапы разработки как аннотированные теги git. Нужен только Python 3 и git.

Запуск:
    python3 tools/stage.py list                         этапы и число коммитов после последнего
    python3 tools/stage.py show 01                      аннотация этапа по номеру или имени
    python3 tools/stage.py close --slug имя --title НАЗВАНИЕ [--epics W001,W002] [--with-tests]
                                                        проверить готовность и поставить тег stage/NN-имя

Этап закрывается только на ветке main или master, при чистом рабочем дереве, чистой проверке документации
(в нее входит трекер работы), новых коммитах после прошлого этапа и, если указано, выполненных эпиках.
Тег ставится локально, в удаленный репозиторий скрипт ничего не отправляет.
Команда тестов задается переменной STAGE_TEST_COMMAND, по умолчанию тесты харнеса.
Код возврата: 0 успех, 2 отказ или неверный вызов.
"""
import argparse
import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PREFIX = "stage/"
SLUG_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
TAG_RE = re.compile(r"stage/(\d{2})-(.+)")
BRANCHES = ("main", "master")
DEFAULT_TESTS = "python3 -m unittest discover -s tests/harness"
FOOTER = re.compile(r"\*\*Версия\*\*: (\d+\.\d+\.\d+)")


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)


def stage_tags():
    tags = [t for t in git("tag", "-l", PREFIX + "*").stdout.split() if TAG_RE.fullmatch(t)]
    return sorted(tags)


def refuse(message):
    print(f"stage: {message}", file=sys.stderr)
    return 2


def tag_info(tag):
    fmt = "%(creatordate:short)|%(objectname:short)|%(*objectname:short)|%(contents:subject)"
    date, _tag_hash, commit, subject = git("for-each-ref", f"--format={fmt}", f"refs/tags/{tag}").stdout.strip().split("|", 3)
    return date, commit, subject


def cmd_list(_args):
    tags = stage_tags()
    if not tags:
        print("этапов еще нет")
        return 0
    for tag in tags:
        number, slug = TAG_RE.fullmatch(tag).groups()
        date, commit, subject = tag_info(tag)
        print(f"{number} {slug}  {date}  {commit}  {subject}")
    last = tags[-1]
    count = git("rev-list", "--count", f"{last}..HEAD").stdout.strip()
    print(f"после этапа {TAG_RE.fullmatch(last).group(1)} коммитов: {count}")
    return 0


def cmd_show(args):
    wanted = args.stage
    matches = [t for t in stage_tags() if TAG_RE.fullmatch(t).group(1) == wanted or TAG_RE.fullmatch(t).group(2) == wanted]
    if not matches:
        return refuse(f"нет этапа {wanted}")
    print(git("tag", "-n99", matches[0]).stdout.rstrip())
    return 0


def work_module():
    spec = importlib.util.spec_from_file_location("work_for_stage", ROOT / "tools" / "work.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.ROOT = ROOT
    return module


def check_epics(ids):
    work = work_module()
    items, _ = work.load_items()
    by_id, children = work.index(items)
    for ident in ids:
        item = by_id.get(ident)
        if item is None or item.get("type") != "эпик":
            return f"нет эпика {ident}"
        status = work.effective_status(item, by_id, children)
        if work.base(status) != "готово":
            return f"эпик {ident} не готов: {status}"
    return None


def constitution_version():
    path = ROOT / ".specify" / "memory" / "constitution.md"
    match = FOOTER.search(path.read_text(encoding="utf-8")) if path.exists() else None
    return match.group(1) if match else "неизвестна"


def cmd_close(args):
    if not SLUG_RE.fullmatch(args.slug):
        return refuse("slug должен быть латиницей в нижнем регистре через дефис, например harness-setup")
    branch = git("branch", "--show-current").stdout.strip()
    if branch not in BRANCHES:
        return refuse(f"этап закрывается на main или master, сейчас ветка {branch or 'не определена'}")
    if git("status", "--porcelain").stdout.strip():
        return refuse("рабочее дерево не чистое, закоммитьте или уберите изменения")
    tags = stage_tags()
    if any(TAG_RE.fullmatch(t).group(2) == args.slug for t in tags):
        return refuse(f"этап с именем {args.slug} уже есть")
    head = git("rev-parse", "HEAD").stdout.strip()
    if tags and git("rev-parse", f"{tags[-1]}^{{commit}}").stdout.strip() == head:
        return refuse("после прошлого этапа нет новых коммитов")
    lint = subprocess.run([sys.executable, "tools/docs-lint.py"], cwd=ROOT, capture_output=True, text=True)
    if lint.returncode != 0:
        print(lint.stdout, file=sys.stderr)
        return refuse("проверка документации не пройдена")
    if args.with_tests:
        tests = subprocess.run(os.environ.get("STAGE_TEST_COMMAND", DEFAULT_TESTS), shell=True, cwd=ROOT,
                               capture_output=True, text=True)
        if tests.returncode != 0:
            print(tests.stdout[-2000:] + tests.stderr[-2000:], file=sys.stderr)
            return refuse("тесты не пройдены")
    epics = [e.strip() for e in args.epics.split(",") if e.strip()]
    problem = check_epics(epics) if epics else None
    if problem:
        return refuse(problem)
    number = int(TAG_RE.fullmatch(tags[-1]).group(1)) + 1 if tags else 1
    name = f"{PREFIX}{number:02d}-{args.slug}"
    adr = len(list((ROOT / "docs" / "decisions").glob("[0-9][0-9][0-9][0-9]-*.md")))
    lines = [args.title, "", f"commit: {head[:12]}", f"constitution: {constitution_version()}", f"adr: {adr}"]
    if epics:
        lines.append("epics: " + ", ".join(epics))
    created = git("tag", "-a", name, "-m", "\n".join(lines))
    if created.returncode != 0:
        return refuse(created.stderr.strip())
    print(f"поставлен тег {name} на {head[:12]}")
    print("тег локальный, отправка в удаленный репозиторий не выполнена")
    return 0


def main():
    parser = argparse.ArgumentParser(prog="stage.py")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("list")
    show = sub.add_parser("show")
    show.add_argument("stage")
    close = sub.add_parser("close")
    close.add_argument("--slug", required=True)
    close.add_argument("--title", required=True)
    close.add_argument("--epics", default="")
    close.add_argument("--with-tests", action="store_true")
    args = parser.parse_args()
    handlers = {"list": cmd_list, "show": cmd_show, "close": cmd_close}
    if args.command not in handlers:
        parser.print_usage(sys.stderr)
        return 2
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Создание ADR по шаблону и перевод в принятые. Нужен только Python 3.

Запуск:
    python3 tools/adr.py new --slug имя --title НАЗВАНИЕ    создать docs/decisions/NNNN-имя.md и строку указателя
    python3 tools/adr.py accept NNNN                         перевести ADR из предложенных в принятые

Номер берется следующий за последним ADR. Шаблон содержит шапку документа, метку актуальности по текущей
версии конституции и разделы с пометкой (заполнить), принять ADR с такой пометкой нельзя.
Код возврата: 0 успех, 2 отказ или неверный вызов.
"""
import argparse
import datetime
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DECISIONS = ROOT / "docs" / "decisions"
INDEX = DECISIONS / "README.md"
SLUG_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
PLACEHOLDER = "(заполнить)"
FOOTER = re.compile(r"\*\*Версия\*\*: (\d+\.\d+\.\d+)")
STATUS_RE = re.compile(r"^\*\*Статус:\*\* (.*)$", re.M)

TEMPLATE = """# ADR {number}: {title}

> **Отвечает за:** причины и альтернативы решения: {title_lower}.
> **Не содержит:** текущих правил, они живут в файлах-владельцах из раздела связанных файлов.
> **Читать когда:** вы меняете или оспариваете это решение.
> **Проверено при версии конституции:** {version}

**Статус:** предложено, {date}.

## Контекст

{todo}

## Решение

{todo}

## Рассмотренные варианты

{todo}

## Последствия

{todo}

## Связанные файлы

{todo}
"""


def refuse(message):
    print(f"adr: {message}", file=sys.stderr)
    return 2


def adr_files():
    return sorted(DECISIONS.glob("[0-9][0-9][0-9][0-9]-*.md"))


def constitution_version():
    path = ROOT / ".specify" / "memory" / "constitution.md"
    match = FOOTER.search(path.read_text(encoding="utf-8")) if path.exists() else None
    return match.group(1) if match else "0.0.0"


def add_index_row(number, filename, title):
    lines = INDEX.read_text(encoding="utf-8").split("\n")
    rows = [i for i, line in enumerate(lines) if re.match(r"\| \[\d{4}\]\(\d{4}-", line)]
    if not rows:
        return False
    lines.insert(rows[-1] + 1, f"| [{number}]({filename}) | {title} | предложено |")
    INDEX.write_text("\n".join(lines), encoding="utf-8")
    return True


def cmd_new(args):
    if not SLUG_RE.fullmatch(args.slug):
        return refuse("slug должен быть латиницей в нижнем регистре через дефис, например frontend-stack")
    title = args.title.strip()
    if not title:
        return refuse("название не может быть пустым")
    if any(p.name[5:-3] == args.slug for p in adr_files()):
        return refuse(f"ADR с именем {args.slug} уже есть")
    files = adr_files()
    number = f"{(int(files[-1].name[:4]) if files else 0) + 1:04d}"
    filename = f"{number}-{args.slug}.md"
    text = TEMPLATE.format(number=number, title=title, title_lower=title[:1].lower() + title[1:],
                           version=constitution_version(), date=datetime.date.today().isoformat(), todo=PLACEHOLDER)
    (DECISIONS / filename).write_text(text, encoding="utf-8")
    if not add_index_row(number, filename, title):
        (DECISIONS / filename).unlink()
        return refuse("в указателе нет строк ADR, шаблон строки взять неоткуда")
    print(f"создан docs/decisions/{filename}, ADR добавлен в указатель со статусом предложено")
    return 0


def cmd_accept(args):
    matches = [p for p in adr_files() if p.name.startswith(args.number + "-")]
    if not matches:
        return refuse(f"нет ADR {args.number}")
    path = matches[0]
    text = path.read_text(encoding="utf-8")
    status = STATUS_RE.search(text)
    if status and status.group(1).startswith("принято"):
        return refuse(f"ADR {args.number} уже принят")
    if PLACEHOLDER in text:
        return refuse(f"в ADR {args.number} остались разделы с пометкой {PLACEHOLDER}, заполнить их нужно до принятия")
    today = datetime.date.today().isoformat()
    text = STATUS_RE.sub(f"**Статус:** принято, {today}.", text, 1)
    path.write_text(text, encoding="utf-8")
    index = INDEX.read_text(encoding="utf-8")
    row = re.compile(rf"^(\| \[{args.number}\]\({re.escape(path.name)}\) \| .*? \| )предложено( \|)$", re.M)
    INDEX.write_text(row.sub(r"\1принято\2", index, 1), encoding="utf-8")
    print(f"ADR {args.number} принят")
    return 0


def main():
    parser = argparse.ArgumentParser(prog="adr.py")
    sub = parser.add_subparsers(dest="command")
    new = sub.add_parser("new")
    new.add_argument("--slug", required=True)
    new.add_argument("--title", required=True)
    accept = sub.add_parser("accept")
    accept.add_argument("number")
    args = parser.parse_args()
    handlers = {"new": cmd_new, "accept": cmd_accept}
    if args.command not in handlers:
        parser.print_usage(sys.stderr)
        return 2
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())

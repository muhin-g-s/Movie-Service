#!/usr/bin/env python3
"""Файловый трекер работы: эпики и элементы в каталоге work/. Нужен только Python 3.

Запуск:
    python3 tools/work.py board                      доска: эпики с прогрессом и их элементы
    python3 tools/work.py next                       элементы, которые можно брать в работу
    python3 tools/work.py show W003                  один элемент с выведенным статусом
    python3 tools/work.py new --type ТИП --title НАЗВАНИЕ --slug имя [--parent W001] [--depends W002,W003]
                              [--kind ВИД] [--priority P2] [--control УРОВЕНЬ] [--status СТАТУС] [--body ТЕКСТ]
                              [--result ПУТЬ] [--resolves "тема реестра; другая тема"]
    python3 tools/work.py link W003 specs/003-имя    привязать элемент-фичу к каталогу фичи Spec Kit
    python3 tools/work.py set W003 [--status СТАТУС] [--result ПУТЬ]   поменять ручной статус или результат
    python3 tools/work.py check                      проверить связность трекера и связь со specs/

Поле result (путь к ADR или документу) есть у исследования, задачи, бага, инцидента и харнеса, готовое исследование
обязано его иметь. Поле resolves называет темы реестра отложенных решений через точку с запятой: пока элемент не готов,
тема в реестре обязана быть, когда готов, ее обязаны удалить из реестра.
Статус фичи со spec не хранится, а выводится из артефактов Spec Kit. Статус эпика выводится из его элементов.
Код возврата: 0 успех, 1 нарушения проверки, 2 неверный вызов.
"""
import argparse
import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TYPES = ("эпик", "фича", "баг", "инцидент", "исследование", "задача", "харнес")
KINDS = ("простой", "исследовательский", "фундамент")
PRIORITIES = ("P1", "P2", "P3")
STATUSES = ("идея", "готово к работе", "в работе", "заблокировано", "готово", "отменено")
KEPT_WITH_SPEC = ("отменено", "заблокировано")
STRICT_KINDS = ("исследовательский", "фундамент")
RESULT_TYPES = ("исследование", "задача", "баг", "инцидент", "харнес")
REGISTRY = "docs/decisions/README.md"
ID_RE = re.compile(r"W\d{3,}")
SLUG_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
TASK_RE = re.compile(r"^\s*- \[( |x|X)\] T\d+", re.M)
SECTIONS = {
    "эпик": ["Цель", "Вне границ", "Критерии готовности"],
    "фича": ["Описание", "Критерии приемки"],
}


class Item:
    def __init__(self, path, fields, body):
        self.path = path
        self.fields = fields
        self.body = body

    def get(self, key):
        return self.fields.get(key, "")

    @property
    def ident(self):
        return self.get("id")

    @property
    def rel(self):
        return str(self.path.relative_to(ROOT))

    @property
    def depends(self):
        raw = self.get("depends").strip()
        raw = raw[1:-1] if raw.startswith("[") and raw.endswith("]") else raw
        return [part.strip() for part in raw.split(",") if part.strip()]


def split_front_matter(text):
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---", 4)
    if end == -1:
        return None
    head = text[4:end].splitlines()
    fields = {}
    for line in head:
        if ":" in line:
            key, _, value = line.partition(":")
            fields[key.strip()] = value.strip()
    return fields, text[end + 4:].lstrip("\n")


def load_items():
    """Элементы по файлам work/*.md: (список элементов, проблемы разбора)."""
    items, problems = [], []
    folder = ROOT / "work"
    for path in sorted(folder.glob("*.md")) if folder.is_dir() else []:
        rel = str(path.relative_to(ROOT))
        parsed = split_front_matter(path.read_text(encoding="utf-8", errors="replace"))
        if parsed is None:
            problems.append((rel, "нет шапки между строками ---"))
            continue
        items.append(Item(path, *parsed))
    return items, problems


def feature_status(spec_dir):
    tasks_file = spec_dir / "tasks.md"
    if tasks_file.is_file():
        marks = TASK_RE.findall(tasks_file.read_text(encoding="utf-8", errors="replace"))
        if marks:
            done = sum(1 for mark in marks if mark != " ")
            if done == len(marks):
                return "готово"
            return f"в работе ({done}/{len(marks)})" if done else f"задачи (0/{len(marks)})"
    if (spec_dir / "plan.md").is_file():
        return "план"
    return "спецификация" if (spec_dir / "spec.md").is_file() else "каталог не найден"


def base(status):
    return status.split(" (")[0]


def effective_status(item, by_id, children, seen=()):
    """Статус элемента: ручной, выведенный из каталога фичи или собранный из потомков эпика."""
    manual = item.get("status")
    if item.get("type") == "эпик":
        if manual == "отменено":
            return "отменено"
        if item.ident in seen:
            return "цикл"
        kids = [by_id[k] for k in children.get(item.ident, []) if k in by_id]
        states = [base(effective_status(kid, by_id, children, seen + (item.ident,))) for kid in kids]
        live = [s for s in states if s != "отменено"]
        done = sum(1 for s in live if s == "готово")
        counts = f"({done}/{len(live)})"
        if live and done == len(live):
            return f"готово {counts}"
        started = any(s in ("спецификация", "план", "задачи", "в работе", "готово") for s in live)
        return f"в работе {counts}" if started else f"не начат {counts}"
    spec = item.get("spec")
    if item.get("type") == "фича" and spec and manual not in KEPT_WITH_SPEC:
        return feature_status(ROOT / spec)
    return manual or "нет статуса"


def index(items):
    by_id, children = {}, {}
    for item in items:
        by_id.setdefault(item.ident, item)
    for item in items:
        if item.get("parent"):
            children.setdefault(item.get("parent"), []).append(item.ident)
    return by_id, children


def find_cycle(edges, start):
    """Цикл по ребрам из start: список вершин или None."""
    stack, seen = [(start, [start])], set()
    while stack:
        node, path = stack.pop()
        for nxt in edges.get(node, []):
            if nxt == start:
                return path + [start]
            if nxt not in seen:
                seen.add(nxt)
                stack.append((nxt, path + [nxt]))
    return None


def check_results(items, by_id, found):
    """Поля result и resolves: результат лежит в файле, тема реестра закрыта вместе с элементом."""
    children = index(items)[1]
    topics = registry_topics()
    for item in items:
        rel, kind_type, result = item.rel, item.get("type"), item.get("result")
        if result and kind_type not in RESULT_TYPES:
            found.append((rel, "result допустим только у исследования, задачи, бага, инцидента и харнеса"))
        status = base(effective_status(item, by_id, children))
        if status == "готово" and kind_type in RESULT_TYPES:
            if not result and kind_type == "исследование":
                found.append((rel, "у готового исследования нужен result"))
            elif result and not (ROOT / result).is_file():
                found.append((rel, f"файл result не найден: {result}"))
        resolves = [t.strip() for t in item.get("resolves").split(";") if t.strip()]
        if not resolves or topics is None or status == "отменено":
            continue
        for topic in resolves:
            if status == "готово" and topic in topics:
                found.append((rel, f"строка осталась в реестре отложенных решений: {topic}"))
            elif status != "готово" and topic not in topics:
                found.append((rel, f"строки нет в реестре отложенных решений: {topic}"))


def problems(levels=None):
    """Нарушения трекера: список (путь, сообщение). levels это допустимые уровни контроля, None отключает проверку."""
    items, found = load_items()
    by_id, _ = index(items)
    owners = {}
    seen_ids = set()
    for item in items:
        rel, ident, kind_type = item.rel, item.ident, item.get("type")

        def bad(message, rel=rel):
            found.append((rel, message))

        for key in ("id", "title", "type", "priority"):
            if not item.get(key):
                bad(f"нет поля {key}")
        if ident and not ID_RE.fullmatch(ident):
            bad("id должен быть вида W001")
        elif ident and not item.path.name.startswith(ident + "-"):
            bad(f"имя файла должно начинаться с id {ident}-")
        if ident in seen_ids:
            bad(f"повторяется id {ident}")
        seen_ids.add(ident)
        if kind_type and kind_type not in TYPES:
            bad(f"неизвестный тип {kind_type}")
        if item.get("kind") and item.get("kind") not in KINDS:
            bad(f"неизвестный вид {item.get('kind')}")
        if item.get("priority") and item.get("priority") not in PRIORITIES:
            bad(f"неизвестный приоритет {item.get('priority')}")
        if item.get("control") and levels is not None and item.get("control") not in levels:
            bad(f"неизвестный уровень контроля {item.get('control')}")
        if item.get("status") and item.get("status") not in STATUSES:
            bad(f"неизвестный статус {item.get('status')}")
        if kind_type == "фича":
            for key in ("kind", "control"):
                if not item.get(key):
                    bad(f"нет поля {key}")
            if item.get("kind") in STRICT_KINDS and item.get("control") == "автономный":
                bad(f"фича вида {item.get('kind')} не может быть автономным (уровень контроля)")
        parent = item.get("parent")
        if parent:
            if parent not in by_id:
                bad(f"parent {parent} не найден")
            elif by_id[parent].get("type") != "эпик":
                bad(f"parent {parent} не эпик")
        for dep in item.depends:
            if dep == ident:
                bad("элемент зависит от самого себя")
            elif dep not in by_id:
                bad(f"depends {dep} не найден")
        spec = item.get("spec")
        if spec:
            if kind_type != "фича":
                bad("spec допустим только у фичи")
            elif not (ROOT / spec).is_dir():
                bad(f"каталог spec не найден: {spec}")
            elif spec in owners:
                bad(f"spec {spec} уже привязан к {owners[spec]}")
            else:
                owners[spec] = ident
            if item.get("status") and item.get("status") not in KEPT_WITH_SPEC and kind_type == "фича":
                bad(f"у фичи со spec ручной статус {item.get('status')}, он выводится из каталога фичи")
        elif kind_type == "эпик":
            pass
        elif not item.get("status"):
            bad("нужен ручной статус, у элемента нет spec")
    check_results(items, by_id, found)
    dep_edges = {i.ident: [d for d in i.depends if d in by_id and d != i.ident] for i in items}
    parent_edges = {i.ident: [i.get("parent")] for i in items if i.get("parent") in by_id}
    for label, edges in (("зависимостей", dep_edges), ("родителей", parent_edges)):
        reported = set()
        for item in items:
            cycle = find_cycle(edges, item.ident)
            if cycle and frozenset(cycle) not in reported:
                reported.add(frozenset(cycle))
                found.append((item.rel, f"цикл {label}: " + " -> ".join(cycle)))
    specs = ROOT / "specs"
    for folder in sorted(p for p in specs.iterdir() if p.is_dir()) if specs.is_dir() else []:
        rel = str(folder.relative_to(ROOT))
        if rel not in owners:
            found.append((rel, f"фича {rel} не привязана к элементу work/, создайте элемент и выполните link"))
    return found


def registry_topics():
    """Темы реестра отложенных решений или None, если реестра нет."""
    path = ROOT / REGISTRY
    if not path.is_file():
        return None
    topics, inside = set(), False
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("## "):
            inside = line.strip() == "## Отложенные решения"
        elif inside and line.startswith("| "):
            cell = line.split("|")[1].strip()
            if cell and cell != "Тема" and not set(cell) <= {"-", " "}:
                topics.add(cell)
    return topics


def lint_levels():
    spec = importlib.util.spec_from_file_location("docs_lint_for_work", ROOT / "tools" / "docs-lint.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.control_levels() or None


def print_problems(found):
    for path, message in found:
        print(f"work: {path}: {message}")


def cmd_check(_args):
    found = problems(lint_levels())
    if found:
        print_problems(found)
        print(f"work: найдено нарушений: {len(found)}")
        return 1
    print(f"work: ok, элементов: {len(load_items()[0])}")
    return 0


def line(item, status, indent):
    return f"{indent}{item.ident} [{item.get('type')}] {item.get('title')} - {status}"


def cmd_board(_args):
    items, _ = load_items()
    by_id, children = index(items)

    def walk(item, depth, seen):
        print(line(item, effective_status(item, by_id, children), "  " * depth))
        for kid in sorted(children.get(item.ident, [])):
            if kid in by_id and kid not in seen:
                walk(by_id[kid], depth + 1, seen | {item.ident})

    roots = [i for i in items if i.get("type") == "эпик" and i.get("parent") not in by_id]
    for item in sorted(roots, key=lambda i: i.ident):
        walk(item, 0, frozenset())
    loose = [i for i in items if i.get("type") != "эпик" and i.get("parent") not in by_id]
    if loose:
        print("без эпика:")
        for item in sorted(loose, key=lambda i: i.ident):
            print(line(item, effective_status(item, by_id, children), "  "))
    if not items:
        print("work: элементов нет")
    return 0


def cmd_next(_args):
    items, _ = load_items()
    by_id, children = index(items)
    ready = []
    for item in items:
        if item.get("type") == "эпик":
            continue
        if base(effective_status(item, by_id, children)) != "готово к работе":
            continue
        if all(dep in by_id and base(effective_status(by_id[dep], by_id, children)) == "готово" for dep in item.depends):
            ready.append(item)
    if not ready:
        print("work: нет элементов, готовых к работе")
        return 0
    print("Можно брать в работу:")
    for item in sorted(ready, key=lambda i: (i.get("priority"), i.ident)):
        print(f"{item.ident}  {item.get('priority')}  {item.get('title')}  ({item.get('type')}, {item.get('kind') or '-'})")
    return 0


def cmd_show(args):
    items, _ = load_items()
    by_id, children = index(items)
    item = by_id.get(args.id)
    if item is None:
        print(f"work: нет элемента {args.id}", file=sys.stderr)
        return 2
    print(f"id: {item.ident}\nзаголовок: {item.get('title')}\nтип: {item.get('type')}")
    print(f"статус: {effective_status(item, by_id, children)}")
    for key, label in (("parent", "эпик"), ("depends", "зависит от"), ("kind", "вид"), ("priority", "приоритет"),
                       ("control", "контроль"), ("spec", "spec"), ("resolves", "решает"),
                       ("result", "результат")):
        if item.get(key):
            print(f"{label}: {item.get(key)}")
    print()
    print(item.body.rstrip())
    return 0


def render(fields, body):
    order = ["id", "title", "type", "parent", "depends", "kind", "priority", "control", "spec", "resolves", "result", "status"]
    head = [f"{key}: {fields[key]}" for key in order if fields.get(key)]
    return "---\n" + "\n".join(head) + "\n---\n\n" + body.strip("\n") + "\n"


def cmd_new(args):
    if not SLUG_RE.fullmatch(args.slug):
        print("work: slug должен быть латиницей в нижнем регистре через дефис, например list-movies", file=sys.stderr)
        return 2
    if args.type not in TYPES:
        print(f"work: неизвестный тип {args.type}, допустимы: {', '.join(TYPES)}", file=sys.stderr)
        return 2
    items, _ = load_items()
    numbers = [int(m.group(1)) for i in items for m in [re.match(r"W(\d+)", i.ident or "")] if m]
    ident = f"W{(max(numbers) if numbers else 0) + 1:03d}"
    status = args.status if args.status else (None if args.type == "эпик" else "идея")
    depends = "[" + ", ".join(p.strip() for p in args.depends.split(",") if p.strip()) + "]" if args.depends else ""
    fields = {"id": ident, "title": args.title, "type": args.type, "parent": args.parent, "depends": depends,
              "kind": args.kind, "priority": args.priority, "control": args.control, "status": status,
              "resolves": args.resolves, "result": args.result}
    names = SECTIONS.get(args.type, ["Описание"])
    body = args.body if args.body else "\n\n".join(f"## {name}\n\nне определено" for name in names)
    path = ROOT / "work" / f"{ident}-{args.slug}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(fields, body), encoding="utf-8")
    own = [m for p, m in problems(lint_levels()) if p == str(path.relative_to(ROOT))]
    if own:
        path.unlink()
        for message in own:
            print(f"work: {message}", file=sys.stderr)
        return 2
    print(f"создан {path.relative_to(ROOT)}")
    return 0


def cmd_link(args):
    items, _ = load_items()
    by_id, _children = index(items)
    item = by_id.get(args.id)
    spec = args.spec.rstrip("/")
    if item is None:
        print(f"work: нет элемента {args.id}", file=sys.stderr)
        return 2
    if item.get("type") != "фича":
        print("work: привязать spec можно только к элементу типа фича", file=sys.stderr)
        return 2
    if item.get("spec"):
        print(f"work: у {args.id} уже есть spec {item.get('spec')}", file=sys.stderr)
        return 2
    if not (ROOT / spec / "spec.md").is_file():
        print(f"work: в {spec} нет spec.md", file=sys.stderr)
        return 2
    owner = next((i.ident for i in items if i.get("spec") == spec), None)
    if owner:
        print(f"work: spec {spec} уже привязан к {owner}", file=sys.stderr)
        return 2
    fields = dict(item.fields)
    fields["spec"] = spec
    if fields.get("status") not in KEPT_WITH_SPEC:
        fields.pop("status", None)
    item.path.write_text(render(fields, item.body), encoding="utf-8")
    print(f"{args.id} привязан к {spec}")
    return 0


def cmd_set(args):
    if not args.status and not args.result:
        print("work: укажите --status или --result", file=sys.stderr)
        return 2
    if args.status and args.status not in STATUSES:
        print(f"work: неизвестный статус {args.status}, допустимы: {', '.join(STATUSES)}", file=sys.stderr)
        return 2
    items, _ = load_items()
    item = index(items)[0].get(args.id)
    if item is None:
        print(f"work: нет элемента {args.id}", file=sys.stderr)
        return 2
    derived = item.get("type") == "эпик" or (item.get("type") == "фича" and item.get("spec"))
    if args.result and item.get("type") not in RESULT_TYPES:
        print("work: result допустим только у исследования, задачи, бага, инцидента и харнеса", file=sys.stderr)
        return 2
    if args.status and derived and args.status not in KEPT_WITH_SPEC + ("отменено",):
        print(f"work: статус {args.id} выводится из артефактов, вручную можно только отменить или заблокировать",
              file=sys.stderr)
        return 2
    fields = dict(item.fields)
    if args.status:
        fields["status"] = args.status
    if args.result:
        fields["result"] = args.result
    item.path.write_text(render(fields, item.body), encoding="utf-8")
    print(f"{args.id}: обновлен")
    return 0


def main():
    parser = argparse.ArgumentParser(prog="work.py")
    sub = parser.add_subparsers(dest="command")
    for name in ("board", "next", "check"):
        sub.add_parser(name)
    show = sub.add_parser("show")
    show.add_argument("id")
    new = sub.add_parser("new")
    new.add_argument("--type", required=True)
    new.add_argument("--title", required=True)
    new.add_argument("--slug", required=True)
    for option in ("parent", "depends", "kind", "control", "status", "body", "resolves", "result"):
        new.add_argument(f"--{option}", default="")
    new.add_argument("--priority", default="P2")
    setter = sub.add_parser("set")
    setter.add_argument("id")
    setter.add_argument("--status", default="")
    setter.add_argument("--result", default="")
    link = sub.add_parser("link")
    link.add_argument("id")
    link.add_argument("spec")
    args = parser.parse_args()
    handlers = {"board": cmd_board, "next": cmd_next, "check": cmd_check, "show": cmd_show, "new": cmd_new,
                "link": cmd_link, "set": cmd_set}
    if args.command not in handlers:
        parser.print_usage(sys.stderr)
        return 2
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())

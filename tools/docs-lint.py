#!/usr/bin/env python3
"""Проверка документации AI-среды. Нужен только Python 3, сторонних пакетов нет.

Запуск:
    python3 tools/docs-lint.py            проверить репозиторий
    python3 tools/docs-lint.py --staged   то же и проверки для git-хука
    python3 tools/docs-lint.py --hook     режим хука Claude Code, читает JSON из stdin
"""
import datetime
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONSTITUTION = ".specify/memory/constitution.md"
MAP_FILE = "docs/README.md"
REGISTRY = "docs/decisions/README.md"
REGISTRY_ANCHOR = "отложенные-решения"
OVERRIDE_TASKS = ".specify/templates/overrides/tasks-template.md"
SETTINGS = ".claude/settings.json"
SKILLS_DIR = ".claude/skills"
# длинное и среднее тире, ё, Ё, угловые кавычки, стрелка, многоточие одним символом
FORBIDDEN = "—–ёЁ«»→…"
HEADER_MARKERS = ("> **Отвечает за:**", "> **Не содержит:**", "> **Читать когда:**")
ALLOWED_PLUGINS = {"oh-my-claudecode@omc"}
FOOTER = re.compile(
    r"^\*\*Версия\*\*: (\d+)\.(\d+)\.(\d+) \| \*\*Ратифицирована\*\*: (\S+) \| \*\*Последняя поправка\*\*: (\S+)$",
    re.M,
)


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8", errors="replace")


def scope_files():
    files = ["README.md", "AGENTS.md", "CLAUDE.md", "code-review.md", CONSTITUTION]
    files += sorted(str(p.relative_to(ROOT)) for p in (ROOT / "docs").rglob("*.md"))
    skills = sorted((ROOT / SKILLS_DIR).glob("*/SKILL.md")) if (ROOT / SKILLS_DIR).is_dir() else []
    files += [str(p.relative_to(ROOT)) for p in skills if not p.parent.name.startswith("speckit-")]
    return [f for f in files if (ROOT / f).exists()]


def strip_comments(text):
    return re.sub(r"<!--.*?-->", "", text, flags=re.S)


def strip_code(text):
    return re.sub(r"```.*?```", "", text, flags=re.S)


def slug(heading):
    heading = re.sub(r"[^\w\s-]", "", heading.strip().lower())
    return re.sub(r"\s", "-", heading)


def anchors(rel):
    text = strip_code(read(rel))
    return {slug(m.group(1)) for m in re.finditer(r"^#{1,6}\s+(.*)$", text, re.M)}


def links(rel):
    """Относительные ссылки файла: (исходный текст, путь цели от корня, якорь)."""
    text = strip_code(strip_comments(read(rel)))
    base = (ROOT / rel).parent
    out = []
    for m in re.finditer(r"\]\(([^)\s]+)\)", text):
        raw = m.group(1)
        if raw.startswith(("http://", "https://", "mailto:")):
            continue
        path, _, frag = raw.partition("#")
        target = (base / path).resolve() if path else (ROOT / rel)
        try:
            target_rel = str(target.relative_to(ROOT))
        except ValueError:
            target_rel = str(target)
        out.append((raw, target_rel, frag))
    return out


def check_encoding(rel, errors):
    if "�" in read(rel):
        errors.append((rel, "файл не в кодировке UTF-8"))


def check_header(rel, errors):
    lines = strip_comments(read(rel)).splitlines()[:25]
    positions = []
    for marker in HEADER_MARKERS:
        found = [i for i, line in enumerate(lines) if line.startswith(marker)]
        if not found:
            errors.append((rel, f"нет строки шапки: {marker}"))
        else:
            positions.append(found[0])
    if positions == sorted(positions) and len(positions) == len(HEADER_MARKERS):
        if positions[2] - positions[0] != 2:
            errors.append((rel, "строки шапки должны идти подряд"))
    elif len(positions) == len(HEADER_MARKERS):
        errors.append((rel, "строки шапки идут не в том порядке"))


def check_links(rel, errors):
    for raw, target_rel, frag in links(rel):
        target = ROOT / target_rel
        if not target.exists():
            errors.append((rel, f"битая ссылка: {raw}"))
        elif frag and target.is_file() and target.suffix == ".md" and frag not in anchors(target_rel):
            errors.append((rel, f"нет якоря в целевом файле: {raw}"))


def check_registry_link(rel, errors):
    if rel == REGISTRY:
        return
    if re.search(r"не определен", read(rel), re.I):
        wanted = any(t == REGISTRY and f == REGISTRY_ANCHOR for _, t, f in links(rel))
        if not wanted:
            errors.append((rel, "есть неопределенные темы, но нет ссылки на реестр отложенных решений"))


def check_forbidden(rel, errors):
    for number, line in enumerate(read(rel).splitlines(), 1):
        for column, char in enumerate(line, 1):
            if char in FORBIDDEN:
                errors.append((rel, f"строка {number}, столбец {column}: запрещенный символ U+{ord(char):04X}"))


def check_map(files, errors):
    mapped = {t for _, t, _ in links(MAP_FILE)}
    for rel in files:
        if rel == MAP_FILE or rel.startswith(SKILLS_DIR + "/"):
            continue
        if rel not in mapped:
            errors.append((rel, "файла нет в карте источников истины docs/README.md"))


def check_constitution(errors):
    match = FOOTER.search(read(CONSTITUTION))
    if not match:
        errors.append((CONSTITUTION, "нет строки версии нужного формата"))
        return
    for value in match.group(4, 5):
        try:
            datetime.date.fromisoformat(value)
        except ValueError:
            errors.append((CONSTITUTION, f"дата не в формате ГГГГ-ММ-ДД: {value}"))


def check_override(errors):
    if not (ROOT / OVERRIDE_TASKS).exists():
        errors.append((OVERRIDE_TASKS, "нет переопределения шаблона задач, тесты должны быть обязательными"))
    elif re.search(r"OPTIONAL|if requested|only if tests requested", read(OVERRIDE_TASKS), re.I):
        errors.append((OVERRIDE_TASKS, "в шаблоне задач тесты названы необязательными, это противоречит принципу VI"))


def check_settings(errors):
    path = ROOT / SETTINGS
    if not path.exists():
        errors.append((SETTINGS, "нет настроек проекта"))
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        errors.append((SETTINGS, f"некорректный JSON: {exc}"))
        return
    plugins = data.get("enabledPlugins", {}) if isinstance(data, dict) else None
    if not isinstance(plugins, dict):
        errors.append((SETTINGS, "enabledPlugins должен быть объектом"))
        return
    for plugin, enabled in plugins.items():
        if enabled and plugin not in ALLOWED_PLUGINS:
            errors.append((SETTINGS, f"включен неразрешенный плагин {plugin}, принцип IX"))


def installed_plugin_notice():
    """Только предупреждение: плагин установлен у пользователя, но не отключен проектом."""
    installed = Path.home() / ".claude" / "plugins" / "installed_plugins.json"
    try:
        names = set(json.loads(installed.read_text(encoding="utf-8")).get("plugins", {}))
        declared = json.loads((ROOT / SETTINGS).read_text(encoding="utf-8")).get("enabledPlugins", {})
    except (OSError, ValueError, AttributeError):
        return []
    return sorted(n for n in names if n not in ALLOWED_PLUGINS and n not in declared)


def harness_files():
    """Файлы харнеса, которые должны быть показаны на схеме связей."""
    found = [SETTINGS, ".specify/extensions.yml"]
    for pattern in (".githooks/*", "tools/*.py", ".specify/templates/overrides/*.md"):
        found += sorted(str(p.relative_to(ROOT)) for p in ROOT.glob(pattern) if p.is_file())
    if (ROOT / SKILLS_DIR).is_dir():
        found += sorted(str(p.parent.relative_to(ROOT)) for p in (ROOT / SKILLS_DIR).glob("*/SKILL.md")
                        if not p.parent.name.startswith("speckit-"))
    return [f for f in found if (ROOT / f).exists()]


def check_harness_diagram(errors):
    match = re.search(r"```mermaid(.*?)```", read(MAP_FILE), re.S)
    if not match:
        errors.append((MAP_FILE, "нет схемы связей в блоке mermaid"))
        return
    for rel in harness_files():
        if rel not in match.group(1):
            errors.append((MAP_FILE, f"файл харнеса {rel} не показан на схеме связей"))


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)


def version_of(text):
    match = FOOTER.search(text)
    return tuple(int(x) for x in match.group(1, 2, 3)) if match else None


def check_staged(errors):
    staged = git("diff", "--cached", "--name-only").stdout.split()
    if CONSTITUTION not in staged:
        return
    content = git("show", f":{CONSTITUTION}").stdout
    if re.match(r"\s*<!--.*?Sync Impact Report.*?-->", content, re.S):
        errors.append((CONSTITUTION, "в коммит попал временный отчет о синхронизации, удалите его"))
    old = git("show", f"HEAD:{CONSTITUTION}")
    if old.returncode != 0:
        return
    old_version, new_version = version_of(old.stdout), version_of(content)
    if new_version is None:
        errors.append((CONSTITUTION, "в индексе нет строки версии нужного формата"))
    elif old_version and new_version <= old_version:
        errors.append((CONSTITUTION, "версия конституции в индексе не больше прежней: "
                       + ".".join(map(str, old_version)) + " -> " + ".".join(map(str, new_version))))


def run_checks(staged):
    errors = []
    files = scope_files()
    for rel in files:
        check_encoding(rel, errors)
        check_header(rel, errors)
        check_links(rel, errors)
        check_registry_link(rel, errors)
        check_forbidden(rel, errors)
    if (ROOT / MAP_FILE).exists():
        check_map(files, errors)
        check_harness_diagram(errors)
    if (ROOT / CONSTITUTION).exists():
        check_constitution(errors)
    check_override(errors)
    check_settings(errors)
    if staged:
        check_staged(errors)
    return errors, files


def hook_main():
    try:
        payload = json.load(sys.stdin)
        edited = payload["tool_input"]["file_path"]
        rel = str(Path(edited).resolve().relative_to(ROOT))
    except (ValueError, KeyError, TypeError, AttributeError, OSError):
        return 0
    if not (rel.endswith(".md") or rel == SETTINGS):
        return 0
    errors, _ = run_checks(staged=False)
    own = [e for e in errors if e[0] == rel]
    for path, message in own:
        print(f"docs-lint: {path}: {message}", file=sys.stderr)
    return 2 if own else 0


def main():
    args = sys.argv[1:]
    if "--hook" in args:
        return hook_main()
    errors, files = run_checks(staged="--staged" in args)
    for path, message in errors:
        print(f"docs-lint: {path}: {message}")
    for name in installed_plugin_notice():
        print(f"docs-lint: предупреждение: плагин {name} установлен, но не отключен в {SETTINGS}")
    if errors:
        print(f"docs-lint: найдено нарушений: {len(errors)}")
        return 1
    print(f"docs-lint: ok, проверено файлов: {len(files)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

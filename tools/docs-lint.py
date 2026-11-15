#!/usr/bin/env python3
"""Проверка согласованности харнеса. Нужен Python 3 и утилиты diff и patch, сторонних пакетов нет.

Запуск:
    python3 tools/docs-lint.py                     проверить репозиторий
    python3 tools/docs-lint.py --staged            то же и проверки для git-хука
    python3 tools/docs-lint.py --hook              режим хука Claude Code после правки, читает JSON из stdin
    python3 tools/docs-lint.py --session           режим хука SessionStart: включает git-хук, показывает нарушения
    python3 tools/docs-lint.py --stamp ФАЙЛ...     поставить метку актуальности после сверки файла с конституцией
    python3 tools/docs-lint.py --update-patches    пересоздать патчи переопределений после осознанной правки
    python3 tools/docs-lint.py --rebuild-overrides пересобрать переопределения из ядра Spec Kit и патчей
"""
import datetime
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONSTITUTION = ".specify/memory/constitution.md"
MAP_FILE = "docs/README.md"
DEV_FILE = "docs/development/README.md"
REGISTRY = "docs/decisions/README.md"
REGISTRY_ANCHOR = "отложенные-решения"
TEMPLATES = ".specify/templates"
OVERRIDES = ".specify/templates/overrides"
EXTENSIONS = ".specify/extensions.yml"
OVERRIDE_TASKS = ".specify/templates/overrides/tasks-template.md"
SETTINGS = ".claude/settings.json"
SKILLS_DIR = ".claude/skills"
# длинное и среднее тире, ё, Ё, угловые кавычки, стрелка, многоточие одним символом
FORBIDDEN = "—–ёЁ«»→…"
HEADER_MARKERS = ("> **Отвечает за:**", "> **Не содержит:**", "> **Читать когда:**")
FRESH_PREFIX = "> **Проверено при версии конституции:**"
FRESH_RE = re.compile(r"^> \*\*Проверено при версии конституции:\*\* (\d+)\.(\d+)\.(\d+)\s*$", re.M)
PLAN_RE = re.compile(r"\*\*Constitution version\*\*:\s*(\d+)\.(\d+)\.(\d+)")
PRINCIPLE_REF = re.compile(r"(?i:принцип)\w*\s+(\b[IVX]+\b(?:(?:\s*,\s*|\s+и\s+|\s*-\s*)\b[IVX]+\b)*)")
ALLOWED_PLUGINS = {"oh-my-claudecode@omc"}
ALLOW_MISSING_PATHS = {".omc/"}
FOOTER = re.compile(
    r"^\*\*Версия\*\*: (\d+)\.(\d+)\.(\d+) \| \*\*Ратифицирована\*\*: (\S+) \| \*\*Последняя поправка\*\*: (\S+)$",
    re.M,
)


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8", errors="replace")


def scope_files():
    files = ["README.md", "AGENTS.md", "CLAUDE.md", "code-review.md", CONSTITUTION]
    files += sorted(str(p.relative_to(ROOT)) for p in (ROOT / "docs").rglob("*.md"))
    skills = sorted((ROOT / SKILLS_DIR).glob("*/*.md")) if (ROOT / SKILLS_DIR).is_dir() else []
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


def version_of(text):
    match = FOOTER.search(text)
    return tuple(int(x) for x in match.group(1, 2, 3)) if match else None


def current_version():
    try:
        return version_of(read(CONSTITUTION))
    except OSError:
        return None


def json_file(rel):
    try:
        return json.loads((ROOT / rel).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# ---------- проверки файлов документации ----------

def check_encoding(rel, errors):
    if "�" in read(rel):
        errors.append((rel, "файл не в кодировке UTF-8"))


def check_header(rel, errors):
    markers = HEADER_MARKERS if rel == CONSTITUTION else HEADER_MARKERS + (FRESH_PREFIX,)
    lines = strip_comments(read(rel)).splitlines()[:25]
    positions = []
    for marker in markers:
        found = [i for i, line in enumerate(lines) if line.startswith(marker)]
        if not found:
            errors.append((rel, f"нет строки шапки: {marker}"))
        else:
            positions.append(found[0])
    if len(positions) == len(markers):
        if positions != sorted(positions):
            errors.append((rel, "строки шапки идут не в том порядке"))
        elif positions[-1] - positions[0] != len(markers) - 1:
            errors.append((rel, "строки шапки должны идти подряд"))


def check_freshness(rel, errors, current):
    if rel == CONSTITUTION or current is None:
        return
    head = "\n".join(strip_comments(read(rel)).splitlines()[:25])
    match = FRESH_RE.search(head)
    if not match:
        return
    marker = tuple(int(x) for x in match.groups())
    shown = ".".join(map(str, marker))
    if marker[:2] < current[:2]:
        errors.append((rel, f"метка актуальности {shown} старше версии конституции {'.'.join(map(str, current))}: "
                            "сверьте файл и выполните python3 tools/docs-lint.py --stamp " + rel))
    elif marker > current:
        errors.append((rel, f"метка актуальности {shown} новее версии конституции"))


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


def check_principle_refs(rel, valid, errors):
    text = strip_code(strip_comments(read(rel)))
    for match in PRINCIPLE_REF.finditer(text):
        for token in re.findall(r"[IVX]+", match.group(1)):
            if token not in valid:
                errors.append((rel, f"ссылка на несуществующий принцип {token}"))


def check_backtick_paths(rel, errors):
    for token in re.findall(r"`([^`\n]+)`", strip_code(read(rel))):
        if (" " in token or "<" in token or "*" in token or "/" not in token
                or token.startswith(("~", "http", "/", "specs/")) or token in ALLOW_MISSING_PATHS):
            continue
        if not (ROOT / token.rstrip("/")).exists():
            errors.append((rel, f"упомянут несуществующий путь {token}"))


# ---------- проверки связей харнеса ----------

def harness_files():
    """Файлы харнеса, которые должны быть показаны на схеме связей и описаны в руководстве."""
    found = [SETTINGS, EXTENSIONS]
    for pattern in (".githooks/*", "tools/*.py", ".specify/templates/overrides/*.md", ".specify/templates/overrides/*.patch"):
        found += sorted(str(p.relative_to(ROOT)) for p in ROOT.glob(pattern) if p.is_file())
    if (ROOT / SKILLS_DIR).is_dir():
        found += sorted(str(p.parent.relative_to(ROOT)) for p in (ROOT / SKILLS_DIR).glob("*/SKILL.md")
                        if not p.parent.name.startswith("speckit-"))
    return [f for f in found if (ROOT / f).exists()]


def check_harness_documented(errors):
    diagram = re.search(r"```mermaid(.*?)```", read(MAP_FILE), re.S)
    if not diagram:
        errors.append((MAP_FILE, "нет схемы связей в блоке mermaid"))
    dev = read(DEV_FILE)
    for rel in harness_files():
        if diagram and rel not in diagram.group(1):
            errors.append((MAP_FILE, f"файл харнеса {rel} не показан на схеме связей"))
        if rel not in dev:
            errors.append((DEV_FILE, f"файл харнеса {rel} не описан в руководстве"))


def check_wiring(errors):
    claude = read("CLAUDE.md") if (ROOT / "CLAUDE.md").exists() else ""
    if not re.search(r"^@AGENTS\.md\s*$", claude, re.M):
        errors.append(("CLAUDE.md", "нет строки импорта @AGENTS.md"))
    settings = json_file(SETTINGS)
    hooks = settings.get("hooks", {}) if isinstance(settings, dict) else {}
    for event, groups in (hooks.items() if isinstance(hooks, dict) else []):
        for group in groups if isinstance(groups, list) else []:
            for hook in group.get("hooks", []) if isinstance(group, dict) else []:
                command = hook.get("command", "") if isinstance(hook, dict) else ""
                for path in re.findall(r'\$CLAUDE_PROJECT_DIR/([^"\s]+)', command):
                    if not (ROOT / path).exists():
                        errors.append((SETTINGS, f"хук {event} ссылается на несуществующий файл {path}"))
    if (ROOT / EXTENSIONS).exists():
        for name in re.findall(r"^\s*command:\s*(\S+)", read(EXTENSIONS), re.M):
            skill = name.replace(".", "-")
            if not (ROOT / SKILLS_DIR / skill / "SKILL.md").exists():
                errors.append((EXTENSIONS, f"команда {name} не соответствует скиллу {skill}"))
    for skill_file in sorted((ROOT / SKILLS_DIR).glob("*/SKILL.md")) if (ROOT / SKILLS_DIR).is_dir() else []:
        if skill_file.parent.name.startswith("speckit-"):
            continue
        match = re.search(r"^name:\s*(\S+)", skill_file.read_text(encoding="utf-8", errors="replace"), re.M)
        rel = str(skill_file.relative_to(ROOT))
        if not match or match.group(1) != skill_file.parent.name:
            errors.append((rel, "имя скилла в шапке не совпадает с именем папки"))
    gitignore = read(".gitignore").splitlines() if (ROOT / ".gitignore").exists() else []
    if ".omc/" not in [line.strip() for line in gitignore]:
        errors.append((".gitignore", "нет строки .omc/"))


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


def diff_text(core, override):
    result = subprocess.run(["diff", "-u", "--label", "core", "--label", "override", str(core), str(override)],
                            capture_output=True, text=True)
    return result.stdout


def check_override_drift(errors):
    folder = ROOT / OVERRIDES
    for override in sorted(folder.glob("*.md")) if folder.is_dir() else []:
        rel = str(override.relative_to(ROOT))
        core = ROOT / TEMPLATES / override.name
        patch = override.with_suffix(".patch")
        if not core.exists():
            errors.append((rel, "нет шаблона ядра Spec Kit для этого переопределения"))
        elif not patch.exists():
            errors.append((rel, "нет файла патча, создайте: python3 tools/docs-lint.py --update-patches"))
        elif diff_text(core, override) != patch.read_text(encoding="utf-8"):
            errors.append((rel, "переопределение расходится с ядром Spec Kit или с патчем: после обновления Spec Kit "
                                "выполните --rebuild-overrides, после осознанной правки файла --update-patches"))


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


def check_plans(errors, current):
    for plan in sorted((ROOT / "specs").glob("*/plan.md")) if (ROOT / "specs").is_dir() else []:
        rel = str(plan.relative_to(ROOT))
        match = PLAN_RE.search(plan.read_text(encoding="utf-8", errors="replace"))
        if not match:
            errors.append((rel, "в плане нет строки Constitution version"))
        elif current and tuple(int(x) for x in match.groups())[:2] < current[:2]:
            errors.append((rel, "план составлен по более старой версии конституции, перепроверьте его "
                                "(например, командой /speckit-analyze) и обновите версию в плане"))


def installed_plugin_notice():
    """Только предупреждение: плагин установлен у пользователя, но не отключен проектом."""
    installed = Path.home() / ".claude" / "plugins" / "installed_plugins.json"
    try:
        names = set(json.loads(installed.read_text(encoding="utf-8")).get("plugins", {}))
        declared = json.loads((ROOT / SETTINGS).read_text(encoding="utf-8")).get("enabledPlugins", {})
    except (OSError, ValueError, AttributeError):
        return []
    return sorted(n for n in names if n not in ALLOWED_PLUGINS and n not in declared)


# ---------- git ----------

def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)


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


# ---------- запуск ----------

def run_checks(staged):
    errors = []
    files = scope_files()
    current = current_version()
    valid = set(re.findall(r"^### ([IVX]+)\.", read(CONSTITUTION), re.M)) if (ROOT / CONSTITUTION).exists() else set()
    for rel in files:
        check_encoding(rel, errors)
        check_header(rel, errors)
        check_freshness(rel, errors, current)
        check_links(rel, errors)
        check_registry_link(rel, errors)
        check_forbidden(rel, errors)
        check_backtick_paths(rel, errors)
        if valid:
            check_principle_refs(rel, valid, errors)
    if (ROOT / MAP_FILE).exists() and (ROOT / DEV_FILE).exists():
        check_map(files, errors)
        check_harness_documented(errors)
    if (ROOT / CONSTITUTION).exists():
        check_constitution(errors)
    check_wiring(errors)
    check_override(errors)
    check_override_drift(errors)
    check_settings(errors)
    check_plans(errors, current)
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


def session_main():
    notes = []
    if git("config", "core.hooksPath").stdout.strip() != ".githooks":
        if git("config", "core.hooksPath", ".githooks").returncode == 0:
            notes.append("Включил git-хук проверки: core.hooksPath=.githooks.")
    errors, _ = run_checks(staged=False)
    if errors:
        first = "; ".join(f"{p}: {m}" for p, m in errors[:8])
        notes.append(f"Харнес несогласован, нарушений: {len(errors)}. Исправь до начала работы или сообщи пользователю. "
                     f"Первые: {first}")
    if notes:
        print("\n".join(notes))
    return 0


def stamp_main(paths):
    current = current_version()
    if current is None or not paths:
        print("docs-lint: нужна версия конституции и список файлов для метки", file=sys.stderr)
        return 2
    scope = set(scope_files())
    version = ".".join(map(str, current))
    status = 0
    for path in paths:
        rel = str((ROOT / path).resolve().relative_to(ROOT))
        if rel not in scope or rel == CONSTITUTION:
            print(f"docs-lint: {path}: файл не подлежит метке", file=sys.stderr)
            status = 2
            continue
        text = read(rel)
        new, count = FRESH_RE.subn(f"{FRESH_PREFIX} {version}", text)
        if not count:
            print(f"docs-lint: {path}: в шапке нет строки метки", file=sys.stderr)
            status = 2
            continue
        (ROOT / rel).write_text(new, encoding="utf-8")
        print(f"метка {version}: {rel}")
    return status


def update_patches_main():
    folder = ROOT / OVERRIDES
    for override in sorted(folder.glob("*.md")):
        core = ROOT / TEMPLATES / override.name
        if not core.exists():
            print(f"docs-lint: нет ядра для {override.name}", file=sys.stderr)
            return 2
        override.with_suffix(".patch").write_text(diff_text(core, override), encoding="utf-8")
        print(f"патч обновлен: {override.with_suffix('.patch').relative_to(ROOT)}")
    return 0


def rebuild_overrides_main():
    folder = ROOT / OVERRIDES
    status = 0
    for patch in sorted(folder.glob("*.patch")):
        core = ROOT / TEMPLATES / (patch.stem + ".md")
        if not core.exists():
            print(f"docs-lint: нет ядра для {patch.name}", file=sys.stderr)
            status = 2
            continue
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out.md"
            result = subprocess.run(["patch", "-s", "-o", str(out), str(core), str(patch)], capture_output=True, text=True)
            if result.returncode != 0:
                print(f"docs-lint: патч {patch.name} не ложится на новое ядро, правьте вручную:\n{result.stdout}{result.stderr}",
                      file=sys.stderr)
                status = 2
                continue
            (folder / (patch.stem + ".md")).write_text(out.read_text(encoding="utf-8"), encoding="utf-8")
            print(f"переопределение пересобрано: {patch.stem}.md")
    return status


def main():
    args = sys.argv[1:]
    if "--hook" in args:
        return hook_main()
    if "--session" in args:
        return session_main()
    if "--stamp" in args:
        return stamp_main([a for a in args if a != "--stamp"])
    if "--update-patches" in args:
        return update_patches_main()
    if "--rebuild-overrides" in args:
        return rebuild_overrides_main()
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

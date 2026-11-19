#!/usr/bin/env python3
"""Проверка и настройка окружения харнеса после клонирования.

Команды:
    python3 tools/harness-setup.py check   проверяет зависимости и печатает команды установки для этой ОС
    python3 tools/harness-setup.py hooks   включает git-хук проекта: core.hooksPath=.githooks

Единственная жесткая зависимость харнеса это Claude Code, скрипт только проверяет его наличие.
Коды возврата: 0 все обязательное на месте, 1 чего-то не хватает, 2 неверный вызов.
Только стандартная библиотека. Переменная HARNESS_SETUP_OS вида "linux-apt" подменяет определение ОС
и пакетного менеджера, она нужна тестам.
"""
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OMC_MARKETPLACE = "https://github.com/Yeachan-Heo/oh-my-claudecode"
OMC_PLUGIN = "oh-my-claudecode@omc"
UV_UNIX = "curl -LsSf https://astral.sh/uv/install.sh | sh"
UV_WINDOWS = 'powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"'
CLAUDE_DOCS = "https://code.claude.com/docs/en/setup"

LINUX_PACKAGES = {
    "apt": "sudo apt-get update && sudo apt-get install -y {packages}",
    "dnf": "sudo dnf install -y {packages}",
    "apk": "apk add {packages}",
    "pacman": "sudo pacman -S --needed {packages}",
    "zypper": "sudo zypper install -y {packages}",
}
LINUX_NAMES = {"python3": {"pacman": "python"}}


def run(args, timeout=60):
    try:
        done = subprocess.run(args, capture_output=True, text=True, timeout=timeout, cwd=ROOT)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return done


def first_line(text):
    return (text or "").strip().splitlines()[0] if (text or "").strip() else ""


def detect():
    """Возвращает (ос, пакетный менеджер). ОС: macos, linux, windows или other."""
    forced = os.environ.get("HARNESS_SETUP_OS")
    if forced:
        name, _, manager = forced.partition("-")
        return name, manager or None
    system = platform.system()
    if system == "Darwin":
        return "macos", "brew" if shutil.which("brew") else None
    if system == "Windows":
        return "windows", "winget" if shutil.which("winget") else None
    if system == "Linux":
        for manager in ("apt-get", "dnf", "apk", "pacman", "zypper"):
            if shutil.which(manager):
                return "linux", manager.replace("apt-get", "apt")
        return "linux", None
    return "other", None


def expected_speckit_version():
    options = ROOT / ".specify" / "init-options.json"
    try:
        return json.loads(options.read_text(encoding="utf-8")).get("speckit_version", "")
    except (OSError, ValueError):
        return ""


def omc_state():
    """Состояние плагина OMC по данным claude plugin list --json: (установлен, включен, версия)."""
    done = run(["claude", "plugin", "list", "--json"])
    if done is None or done.returncode != 0:
        return False, False, ""
    try:
        plugins = json.loads(done.stdout)
    except ValueError:
        return False, False, ""
    for plugin in plugins if isinstance(plugins, list) else []:
        if str(plugin.get("id", "")).startswith("oh-my-claudecode@"):
            return True, bool(plugin.get("enabled")), str(plugin.get("version", ""))
    return False, False, ""


def hooks_enabled():
    done = run(["git", "config", "core.hooksPath"])
    return done is not None and done.stdout.strip() == ".githooks"


def tool_version(name):
    if not shutil.which(name):
        return None
    done = run([name, "--version"])
    return first_line(done.stdout or done.stderr) if done is not None else "есть"


def install_commands(os_name, manager, missing, speckit_version):
    """Команды установки недостающего для этой ОС, в порядке выполнения."""
    commands = []
    system_tools = [t for t in ("git", "bash", "diff", "patch", "make", "python3") if t in missing]
    if system_tools:
        if os_name == "macos":
            if any(t in system_tools for t in ("git", "diff", "patch", "bash", "make")):
                commands.append("xcode-select --install")
            if "python3" in system_tools:
                commands.append("brew install python" if manager == "brew" else f"{UV_UNIX} && uv python install --default")
        elif os_name == "linux":
            template = LINUX_PACKAGES.get(manager or "")
            packages = " ".join(LINUX_NAMES.get(t, {}).get(manager, {"diff": "diffutils"}.get(t, t)) for t in system_tools)
            if template:
                commands.append(template.format(packages=packages))
            else:
                commands.append(f"установите пакетным менеджером дистрибутива: {packages}")
        elif os_name == "windows":
            if any(t in system_tools for t in ("git", "diff", "patch", "bash")):
                commands.append("winget install --id Git.Git -e")
            if "make" in system_tools:
                commands.append("winget install --id GnuWin32.Make -e")
            if "python3" in system_tools:
                commands.append(f"winget install --id astral-sh.uv -e" if manager == "winget" else UV_WINDOWS)
                commands.append("uv python install --default")
        else:
            commands.append("установите вручную: " + " ".join(system_tools))
    if "uv" in missing and ("specify" in missing or "python3" in system_tools):
        commands.append(UV_WINDOWS if os_name == "windows" else UV_UNIX)
    if "specify" in missing:
        pin = f"'specify-cli=={speckit_version}'" if speckit_version else "specify-cli"
        commands.append(f"uv tool install {pin}")
    if "omc" in missing:
        commands.append(f"claude plugin marketplace add {OMC_MARKETPLACE}")
        commands.append(f"claude plugin install {OMC_PLUGIN}")
    if "hooks" in missing:
        commands.append("python3 tools/harness-setup.py hooks")
    return commands


def check_main():
    os_name, manager = detect()
    speckit_version = expected_speckit_version()
    rows = []
    missing = []

    def row(key, title, required, found, note=""):
        status = "ok" if found else ("НЕТ" if required else "нет, необязательно")
        rows.append((status, title, note))
        if not found:
            missing.append(key)

    claude = tool_version("claude")
    row("claude", "Claude Code", True, bool(claude), claude or f"единственная жесткая зависимость, установка: {CLAUDE_DOCS}")
    for name, title in (("git", "git"), ("bash", "bash"), ("diff", "diff"), ("patch", "patch"), ("make", "make")):
        version = tool_version(name)
        hint = "" if name == "make" else "на Windows дает Git for Windows"
        row(name, title, True, bool(version), version or (hint if os_name == "windows" else ""))
    python = shutil.which("python3")
    row("python3", "python3 по имени, его вызывают хуки", True, bool(python),
        f"{platform.python_version()} в {python}" if python else "команда python3 не найдена в PATH")
    installed, enabled, version = omc_state()
    row("omc", "плагин OMC", True, installed and enabled,
        f"версия {version}" + ("" if enabled else ", выключен: claude plugin enable " + OMC_PLUGIN) if installed
        else ("Claude Code не найден, проверить нельзя" if not claude else "не установлен"))
    specify = tool_version("specify")
    note = specify or f"нужен только для цепочки feature-chain, версия проекта {speckit_version or 'не задана'}"
    if specify and speckit_version and speckit_version not in specify:
        note += f", в проекте {speckit_version}"
    row("specify", "Spec Kit CLI (specify)", False, bool(specify), note)
    uv = tool_version("uv")
    row("uv", "uv", False, bool(uv), uv or "ставит Spec Kit CLI и Python там, где их нет")
    row("hooks", "git-хук проекта core.hooksPath=.githooks", True, hooks_enabled(), "" if hooks_enabled() else "включает команда hooks")

    print(f"ОС: {os_name}, пакетный менеджер: {manager or 'не найден'}")
    for status, title, note in rows:
        print(f"  [{status}] {title}" + (f": {note}" if note else ""))
    required_missing = [k for k in missing if k not in ("specify", "uv")]
    commands = install_commands(os_name, manager, missing, speckit_version)
    if commands:
        print("Команды для этой ОС, по порядку:")
        for command in commands:
            print(f"  {command}")
        print("После установки плагина или смены настроек перезапустите Claude Code и повторите check.")
        print("Если uv или specify ставились в ~/.local/bin, добавьте эту папку в PATH.")
    if "claude" in missing:
        print("Сначала установите Claude Code, без него харнес не работает.")
    if required_missing:
        print(f"harness-setup: не хватает обязательного: {', '.join(required_missing)}")
        return 1
    print("harness-setup: ok, все обязательное на месте")
    return 0


def hooks_main():
    if hooks_enabled():
        print("git-хук уже включен: core.hooksPath=.githooks")
        return 0
    done = run(["git", "config", "core.hooksPath", ".githooks"])
    if done is None or done.returncode != 0:
        print("harness-setup: не удалось выполнить git config core.hooksPath .githooks", file=sys.stderr)
        return 1
    print("Включил git-хук: core.hooksPath=.githooks")
    return 0


def main(argv):
    command = argv[1] if len(argv) > 1 else "check"
    if command == "check" and len(argv) <= 2:
        return check_main()
    if command == "hooks" and len(argv) <= 2:
        return hooks_main()
    print(__doc__.strip(), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))

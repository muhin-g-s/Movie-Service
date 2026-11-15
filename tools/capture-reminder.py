#!/usr/bin/env python3
"""Напоминания о записи знаний из чата. Вызывается хуками Claude Code.

    stop      Stop: раз в N ходов просит агента проверить незаписанные решения
    end       SessionEnd: помечает длинную сессию как неразобранную
    compact   PreCompact: сжатие тоже означает длинную сессию
    start     SessionStart: напоминает о неразобранных сессиях
    clear     снимает напоминание, его вызывают /capture и /harvest

Хуки SessionEnd и PreCompact ничего не передают модели, поэтому только ставят пометку.
Состояние лежит вне репозитория: ~/.claude/projects/<проект>/capture/
Частота напоминания в ходах: переменная окружения CAPTURE_REMIND_EVERY, по умолчанию 12.
"""
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MIN_TURNS = 6
KEEP_DAYS = 30
REMINDER = (
    "Проверь, нет ли в этой сессии решений и фактов проекта, которых еще нет в файлах репозитория. "
    "Если есть, перечисли их и предложи пользователю запустить /capture. "
    "Если нет, ответь одним предложением: незаписанных решений нет."
)


def state_dir():
    encoded = re.sub(r"[^A-Za-z0-9]", "-", str(ROOT))
    path = Path.home() / ".claude" / "projects" / encoded / "capture"
    path.mkdir(parents=True, exist_ok=True)
    return path


def read_payload():
    try:
        data = json.load(sys.stdin)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def session_id(payload):
    return re.sub(r"[^\w-]", "", str(payload.get("session_id", "unknown")))[:64] or "unknown"


def turns_file(payload):
    return state_dir() / f"{session_id(payload)}.turns"


def read_turns(payload):
    try:
        return int(turns_file(payload).read_text())
    except (OSError, ValueError):
        return 0


def mark_pending(payload, reason):
    line = json.dumps({"session": session_id(payload), "time": int(time.time()), "reason": reason}, ensure_ascii=False)
    with open(state_dir() / "pending", "a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def cleanup():
    limit = time.time() - KEEP_DAYS * 86400
    for path in state_dir().glob("*.turns"):
        if path.stat().st_mtime < limit:
            path.unlink()


def cmd_stop(payload):
    if payload.get("stop_hook_active"):
        return 0
    every = int(os.environ.get("CAPTURE_REMIND_EVERY", "12") or 12)
    count = read_turns(payload) + 1
    turns_file(payload).write_text(str(count))
    if every > 0 and count % every == 0:
        print(REMINDER, file=sys.stderr)
        return 2
    return 0


def cmd_end(payload):
    if read_turns(payload) >= MIN_TURNS:
        mark_pending(payload, str(payload.get("reason", "end")))
    return 0


def cmd_compact(payload):
    mark_pending(payload, "compact")
    return 0


def cmd_start(payload):
    pending = state_dir() / "pending"
    if pending.exists():
        count = len([line for line in pending.read_text(encoding="utf-8").splitlines() if line.strip()])
        print(f"Напоминание проекта: в {count} прошлых сессиях знания из чата могли остаться незаписанными. "
              "Предложи пользователю запустить /harvest. "
              f"Снять напоминание: python3 tools/capture-reminder.py clear")
    cleanup()
    return 0


def cmd_clear(_payload):
    pending = state_dir() / "pending"
    if pending.exists():
        pending.unlink()
    print("напоминание снято")
    return 0


COMMANDS = {"stop": cmd_stop, "end": cmd_end, "compact": cmd_compact, "start": cmd_start, "clear": cmd_clear}


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in COMMANDS:
        print(__doc__, file=sys.stderr)
        return 1
    payload = {} if sys.argv[1] == "clear" else read_payload()
    try:
        return COMMANDS[sys.argv[1]](payload)
    except OSError as exc:
        print(f"capture-reminder: {exc}", file=sys.stderr)
        return 0


if __name__ == "__main__":
    sys.exit(main())

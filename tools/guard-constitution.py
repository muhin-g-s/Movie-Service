#!/usr/bin/env python3
"""Хук Claude Code перед командой Bash: просит подтверждение, если команда похожа на запись в конституцию.

Читает JSON хука из stdin. Это эвристика по тексту команды, а не гарантия:
последняя защита это проверка версии в git-хуке. Только чтение (cat, grep, git diff) не трогает.
"""
import json
import re
import sys

NAME = "memory/constitution.md"
WRITES = re.compile(
    r"(?<!\d)>(?![&\s]*(?:/dev/null|&?\d))"
    r"|\b(?:tee|mv|cp|rm|vim?|nano|ed|truncate|dd|install|ln|curl|wget|python3?|perl|ruby|node)\b"
    r"|\bsed\b[^|]*\s-i"
    r"|\bgit\s+(?:checkout|restore|reset|stash|apply|rm|mv)\b"
)


def main():
    try:
        payload = json.load(sys.stdin)
        command = payload["tool_input"]["command"]
    except (ValueError, KeyError, TypeError):
        return 0
    if isinstance(command, str) and NAME in command and WRITES.search(command):
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "ask",
            "permissionDecisionReason": "Команда может изменить конституцию. Поправки идут через /speckit-constitution с повышением версии.",
        }}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

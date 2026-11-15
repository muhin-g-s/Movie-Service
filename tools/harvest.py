#!/usr/bin/env python3
"""Поиск повторяющегося контекста в сессиях Claude Code и список автопамяти проекта.

Только читает и печатает в терминал, ничего не пишет и никуда не отправляет.
Нужен только Python 3, сторонних пакетов нет.

Запуск:
    python3 tools/harvest.py [--min 2] [--words 7] [--limit 15]
"""
import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORD = re.compile(r"[\w'-]+", re.U)
SKIP_PREFIXES = ("<command-", "<local-command", "<task-notification", "[SYSTEM", "Base directory for this skill",
                 "Another Claude session", "<agent-message")
TAG_ONLY = re.compile(r"</?pasted_content[^>]*>")
INLINE_NOISE = re.compile(r"<(system-reminder|local-command-caveat|task-notification)>.*?</\1>", re.S)


def project_dir():
    encoded = re.sub(r"[^A-Za-z0-9]", "-", str(ROOT))
    return Path.home() / ".claude" / "projects" / encoded


def prompts(project):
    """Реплики пользователя из расшифровок: (id сессии, текст)."""
    for path in sorted(project.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not isinstance(row, dict) or row.get("type") != "user" or row.get("isSidechain"):
                continue
            message = row.get("message")
            content = message.get("content") if isinstance(message, dict) else None
            if isinstance(content, list):
                content = "\n".join(c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text")
            if not isinstance(content, str):
                continue
            text = TAG_ONLY.sub("", INLINE_NOISE.sub("", content)).strip()
            if text and not text.startswith(SKIP_PREFIXES):
                yield path.stem, text


def repo_text():
    parts = []
    for path in ROOT.rglob("*.md"):
        rel = path.relative_to(ROOT)
        if rel.parts[0] in (".git", ".omc"):
            continue
        parts.append(" ".join(w.lower() for w in WORD.findall(path.read_text(encoding="utf-8", errors="replace"))))
    return " ".join(parts)


def repeated_phrases(messages, size, minimum):
    """Максимальные повторяющиеся фразы: ключ -> (сообщения, сессии, исходный текст)."""
    tokens = []
    where = {}
    for index, (session, text) in enumerate(messages):
        words = WORD.findall(text)
        norm = [w.lower() for w in words]
        tokens.append((words, norm, session))
        for i in range(len(norm) - size + 1):
            where.setdefault(tuple(norm[i:i + size]), set()).add(index)
    found = {}
    for index, (words, norm, session) in enumerate(tokens):
        i = 0
        while i <= len(norm) - size:
            if len(where[tuple(norm[i:i + size])]) >= minimum:
                j = i
                while j + 1 <= len(norm) - size and len(where[tuple(norm[j + 1:j + 1 + size])]) >= minimum:
                    j += 1
                key = " ".join(norm[i:j + size])
                entry = found.setdefault(key, [set(), set(), " ".join(words[i:j + size])])
                entry[0].add(index)
                entry[1].add(session)
                i = j + size
            else:
                i += 1
    return found


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--min", type=int, default=2, help="минимум сообщений с повтором")
    parser.add_argument("--words", type=int, default=7, help="минимальная длина фразы в словах")
    parser.add_argument("--limit", type=int, default=15, help="сколько кандидатов показать")
    args = parser.parse_args()

    project = project_dir()
    print(f"Каталог сессий: {project}")
    messages = list(prompts(project)) if project.is_dir() else []
    print(f"Реплик пользователя: {len(messages)}")
    if not messages and project.is_dir() and any(project.glob("*.jsonl")):
        print("ПРЕДУПРЕЖДЕНИЕ: расшифровки есть, но реплик пользователя не найдено. "
              "Формат расшифровок внутренний и мог измениться, скрипт нужно обновить.")

    existing = repo_text()
    found = repeated_phrases(messages, args.words, args.min) if messages else {}
    new = {k: v for k, v in found.items() if k not in existing}
    ranked = sorted(new.items(), key=lambda kv: (len(kv[1][0]) * len(kv[0].split())), reverse=True)[:args.limit]
    print(f"\nПовторяющиеся фразы, которых нет в документации репозитория: {len(new)}")
    for key, (msgs, sessions, original) in ranked:
        shown = original if len(original) <= 200 else original[:197] + "..."
        print(f"- повторов: {len(msgs)}, сессий: {len(sessions)}\n  {shown}")

    memory = project / "memory"
    print("\nПамять Claude для проекта:")
    files = sorted(memory.glob("*.md")) if memory.is_dir() else []
    if not files:
        print("- пусто")
    for path in files:
        lines = [l.strip() for l in path.read_text(encoding="utf-8", errors="replace").splitlines() if l.strip()]
        print(f"- {path.name}: {lines[0][:120] if lines else '(пусто)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Заглушка команды claude для тестов раннера сценариев.

Поведение задает JSON-файл из переменной FAKE_CLAUDE_SPEC: какие файлы создать в рабочей копии,
какие вызовы инструментов и какой текст вывести, какой код возврата вернуть.
"""
import json
import os
import sys
from pathlib import Path

spec = json.load(open(os.environ["FAKE_CLAUDE_SPEC"], encoding="utf-8"))
model = sys.argv[sys.argv.index("--model") + 1] if "--model" in sys.argv else ""
spec = {**spec, **spec.get("by_model", {}).get(model, {})}
for rel, text in spec.get("write_files", {}).items():
    target = Path(rel)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
if spec.get("exit_code"):
    sys.stderr.write(spec.get("stderr", "ошибка"))
    sys.exit(spec["exit_code"])
print(json.dumps({"type": "system", "subtype": "init", **spec.get("init", {})}, ensure_ascii=False))
for tool in spec.get("tools", []):
    block = {"type": "tool_use", "name": tool["name"], "input": tool.get("input", {})}
    print(json.dumps({"type": "assistant", "message": {"content": [block]}}, ensure_ascii=False))
print(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": spec.get("text", "")}]}},
                 ensure_ascii=False))
print(json.dumps({"type": "result", "result": spec.get("result", ""), "is_error": False,
                  "total_cost_usd": spec.get("cost", 0.01), "num_turns": spec.get("turns", 1)}, ensure_ascii=False))

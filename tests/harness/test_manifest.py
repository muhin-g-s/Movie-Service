"""Эталонный манифест харнеса: набор файлов и проводка хуков меняются только осознанно.

Если тест упал, харнес изменился. Проверьте, что для изменения есть тесты, и обновите манифест:
    UPDATE_MANIFEST=1 python3 -m unittest discover -s tests/harness -p 'test_manifest.py'
"""
import json
import os
import re
import unittest

from helpers import ROOT, load_tool

MANIFEST = ROOT / "tests" / "harness" / "manifest.json"


def current_inventory():
    lint = load_tool("docs-lint")
    settings = json.loads((ROOT / ".claude/settings.json").read_text(encoding="utf-8"))
    hooks = {event: sorted(h["command"].replace("$CLAUDE_PROJECT_DIR/", "<проект>/") for g in groups for h in g["hooks"])
             for event, groups in settings["hooks"].items()}
    extensions = (ROOT / ".specify/extensions.yml").read_text(encoding="utf-8")
    return {
        "files": lint.harness_files(),
        "hooks": hooks,
        "spec_kit_commands": sorted(re.findall(r"^\s*command:\s*(\S+)", extensions, re.M)),
        "disabled_plugins": sorted(k for k, v in settings["enabledPlugins"].items() if not v),
        "permissions": {k: sorted(v) for k, v in settings["permissions"].items()},
    }


class TestManifest(unittest.TestCase):
    def test_inventory_matches_manifest(self):
        inventory = current_inventory()
        if os.environ.get("UPDATE_MANIFEST") == "1":
            MANIFEST.write_text(json.dumps(inventory, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        expected = json.loads(MANIFEST.read_text(encoding="utf-8"))
        self.assertEqual(inventory, expected,
                         "харнес изменился: проверьте тесты и обновите манифест командой из описания модуля")


if __name__ == "__main__":
    unittest.main()

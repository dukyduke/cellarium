"""Static front-end checks: i18n completeness and module imports (no browser needed)."""

import json
import re
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "cellarium" / "static"
JS = sorted((STATIC / "js").rglob("*.js"))


def load(lang):
    return json.loads((STATIC / "i18n" / f"{lang}.json").read_text(encoding="utf-8"))


def test_languages_have_same_keys():
    fr, en = load("fr"), load("en")
    assert set(fr) == set(en), sorted(set(fr) ^ set(en))
    assert all(v.strip() for v in fr.values()) and all(v.strip() for v in en.values())


def test_every_static_key_is_translated():
    fr = load("fr")
    missing = []
    for path in JS:
        for key in re.findall(r"\bt\('([^']+)'", path.read_text(encoding="utf-8")):
            if key not in fr:
                missing.append(f"{path.name}: {key}")
    assert not missing, missing


def test_dynamic_key_families_are_complete():
    fr = load("fr")
    families = {
        "phase.{}": ["keep", "drink", "peak", "decline", "unknown"],
        "phase.{}.help": ["keep", "drink", "peak", "decline", "unknown"],
        "out.{}": ["drunk", "gifted", "lost"],
        "in.{}": ["purchase", "import"],
        "role.{}": ["owner", "reader"],
        "history.kind.{}": ["in", "out", "move", "restore"],
        "cellar.mode.{}": ["list", "schedule", "plan", "history"],
        "import.mode.{}": ["skip_existing", "add", "replace"],
        "settings.{}": ["storages", "import", "users", "tokens", "account", "types", "about"],
        "window.{}": ["drink_from", "peak_from", "peak_to", "drink_before"],
        "wine.{}": ["appellation", "producer", "region", "country", "grapes", "serving_temp", "description",
                    "food_pairing", "notes"],
    }
    missing = [pattern.format(v) for pattern, values in families.items() for v in values if pattern.format(v) not in fr]
    assert not missing, missing


def test_named_imports_exist():
    exports = {}
    for path in JS:
        text = path.read_text(encoding="utf-8")
        names = set(re.findall(r"export (?:async )?(?:function|const|class|let) (\w+)", text))
        for group in re.findall(r"export \{([^}]+)\}", text):
            names |= {n.strip().split(" as ")[-1] for n in group.split(",")}
        exports[path.resolve()] = names
    problems = []
    for path in JS:
        text = path.read_text(encoding="utf-8")
        for group, src in re.findall(r"import \{([^}]+)\} from '([^']+)'", text):
            target = (path.parent / src).resolve()
            for name in [n.strip() for n in group.split(",") if n.strip()]:
                if name not in exports.get(target, set()):
                    problems.append(f"{path.name}: {name} from {src}")
    assert not problems, problems


def test_enrichment_skill_copy_is_in_sync():
    """skills/cellarium-wine-enrichment/SKILL.md mirrors the guide served by the MCP server."""
    root = STATIC.parent.parent
    served = (root / "cellarium" / "guides" / "wine-enrichment.md").read_text(encoding="utf-8")
    copy = (root / "skills" / "cellarium-wine-enrichment" / "SKILL.md").read_text(encoding="utf-8")
    assert served == copy, "copy cellarium/guides/wine-enrichment.md to skills/cellarium-wine-enrichment/SKILL.md"
    assert served.startswith("---\nname: cellarium-wine-enrichment\n")

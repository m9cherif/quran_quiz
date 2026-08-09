"""Static-asset regression guards (no server required)."""

import re
from pathlib import Path

JS_DIR = Path(__file__).resolve().parent.parent / "static" / "js"

# Script bundles as loaded by each page (see templates/base.html blocks).
BUNDLES = {
    "app+join": ["app.js", "join.js"],
    "app+admin": ["app.js", "admin.js"],
    "app+room": ["app.js", "room.js"],
}

GLOBAL_DECL = re.compile(
    r"^(?:const|let|class)\s+([A-Za-z_$][A-Za-z0-9_$]*)\b", re.MULTILINE
)


def _declared_names(bundle):
    names = {}
    for script in bundle:
        source = (JS_DIR / script).read_text(encoding="utf-8")
        for match in GLOBAL_DECL.finditer(source):
            names.setdefault(match.group(1), []).append(script)
    return names


def test_no_duplicate_global_declarations_in_shared_scope():
    for label, bundle in BUNDLES.items():
        collisions = {
            name: scripts
            for name, scripts in _declared_names(bundle).items()
            if len({s for s in scripts}) > 1
        }
        assert not collisions, (
            f"Collision globale dans le bundle {label}: {collisions}. "
            "Deux const/let du même nom dans la même page = SyntaxError "
            "au chargement, tous les boutons deviennent muets."
        )
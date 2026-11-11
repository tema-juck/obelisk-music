"""Player remains the existing root package; Radio is an independent uv project."""

import ast
import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_player_runtime_does_not_import_or_bundle_radio():
    for path in (ROOT / "src/obelisk_music").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom):
                assert "radio" not in (node.module or "")
            if isinstance(node, ast.Import):
                assert not any("obelisk_radio" in n.name for n in node.names)
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert project["project"]["name"] == "obelisk-music"
    assert project["project"]["version"] == "0.3.0a2"
    assert project["project"]["scripts"] == {"obelisk": "obelisk_music.app:main"}
    assert "workspace" not in project.get("tool", {}).get("uv", {})
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    assert "obelisk-radio" not in {p["name"] for p in lock["package"]}


def test_radio_is_a_separate_project_with_original_provenance():
    root = ROOT / "apps/radio"
    project = tomllib.loads((root / "pyproject.toml").read_text())
    assert project["project"]["scripts"] == {"obelisk-radio": "obelisk_radio.app:main"}
    assert (
        len(json.loads((root / "PROVENANCE.json").read_text())["original_commits"]) == 7
    )
    assert (root / "uv.lock").is_file()

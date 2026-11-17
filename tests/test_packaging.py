"""Packaging checks: what a ``pip install`` user actually gets."""

import json
from pathlib import Path

from cad2revit_mcp.samples import build_sample


def test_rule_set_is_bundled_with_the_package():
    packaged = Path(__file__).resolve().parents[1] / "src" / "cad2revit_mcp" / "rules"
    rules = json.loads((packaged / "structure_default.json").read_text(encoding="utf-8"))
    assert rules["layers"]["column"]
    assert "graph" in rules


def test_repo_and_package_rule_copies_do_not_drift():
    root = Path(__file__).resolve().parents[1]
    repo = json.loads((root / "rules" / "structure_default.json").read_text(encoding="utf-8"))
    packaged = json.loads(
        (root / "src" / "cad2revit_mcp" / "rules" / "structure_default.json")
        .read_text(encoding="utf-8")
    )
    assert repo == packaged, "rules/ 与包内副本不一致，请同步后再发布"


def test_sample_builder_is_importable_from_the_package(tmp_path):
    path = build_sample(tmp_path / "sample.dxf")
    assert path.exists() and path.stat().st_size > 0

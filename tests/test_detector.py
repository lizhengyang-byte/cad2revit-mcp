from cad2revit_mcp.engine.detector.detect import detect_components
from cad2revit_mcp.engine.detector.rules import load_rules
from cad2revit_mcp.engine.parser.dxf_parser import parse_dxf


def test_detects_four_types(config, sample_path):
    parsed = parse_dxf(sample_path, config)
    rules = load_rules()
    elements, ambiguities, stats = detect_components(parsed, rules, 0.75)
    by_type = stats["by_type"]
    assert by_type["grid"] == 2
    assert by_type["column"] == 2
    assert by_type["wall"] == 1
    assert by_type["beam"] == 1
    assert stats["total"] == 6


def test_column_has_evidence_and_high_confidence(config, sample_path):
    parsed = parse_dxf(sample_path, config)
    elements, _, _ = detect_components(parsed, load_rules(), 0.75)
    columns = [e for e in elements if e.type == "column"]
    assert all(c.confidence >= 0.9 for c in columns)
    assert all("layer" in c.evidence for c in columns)
    assert all(c.geometry["section"] == [600.0, 600.0] for c in columns)


def test_wall_thickness_from_pair_distance(config, sample_path):
    parsed = parse_dxf(sample_path, config)
    elements, _, _ = detect_components(parsed, load_rules(), 0.75)
    walls = [e for e in elements if e.type == "wall"]
    assert len(walls) == 1
    assert walls[0].geometry["thickness"] == 200.0


def test_beam_uses_label_section(config, sample_path):
    parsed = parse_dxf(sample_path, config)
    elements, _, _ = detect_components(parsed, load_rules(), 0.75)
    beams = [e for e in elements if e.type == "beam"]
    assert beams[0].params["width"] == 250.0
    assert beams[0].params["depth"] == 600.0

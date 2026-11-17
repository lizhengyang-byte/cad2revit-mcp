from cad2revit_mcp.engine.detector.detect import detect_components
from cad2revit_mcp.engine.detector.rules import load_rules
from cad2revit_mcp.engine.parser.dxf_parser import parse_dxf
from cad2revit_mcp.engine.verifier import verify
from cad2revit_mcp.models import IRDocument


def test_verify_reports_missing_and_metrics(config, sample_path):
    parsed = parse_dxf(sample_path, config)
    elements, ambiguous, _ = detect_components(parsed, load_rules(), 0.75)
    ir = IRDocument(task_id="t", sheet=parsed.sheets[0], elements=elements)
    execution = {"created": [{"element_id": elements[0].id}], "failed": [], "skipped": []}
    result = verify(ir, execution)
    assert len(result["checks"]["missing"]) == len(elements) - 1
    assert 0 < result["metrics"]["auto_ratio"] < 1


def test_verify_detects_offset():
    from cad2revit_mcp.models import Element

    element = Element("C-001", "column", {"insert": [0, 0], "section": [600, 600]},
                      {"material": "C30"})
    ir = IRDocument(task_id="t", sheet={}, elements=[element])
    execution = {"created": [{"element_id": "C-001", "position": [120, 0]}]}
    result = verify(ir, execution, tolerance_mm=50)
    assert result["checks"]["offset"][0]["deviation_mm"] == 120.0

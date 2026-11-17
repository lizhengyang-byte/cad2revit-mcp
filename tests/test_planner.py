from cad2revit_mcp.engine.detector.detect import detect_components
from cad2revit_mcp.engine.detector.rules import load_rules
from cad2revit_mcp.engine.parser.dxf_parser import parse_dxf
from cad2revit_mcp.engine.planner import plan
from cad2revit_mcp.models import IRDocument


def _ir(config, sample_path):
    parsed = parse_dxf(sample_path, config)
    elements, ambiguous, _ = detect_components(parsed, load_rules(), 0.75)
    return IRDocument(task_id="t-test", sheet=parsed.sheets[0], elements=elements,
                      ambiguous=[a["element_id"] for a in ambiguous])


def test_plan_maps_every_element(config, sample_path):
    ir = _ir(config, sample_path)
    result = plan(ir, config, batch_size=2)
    assert result["stats"]["planned"] == 6
    assert result["stats"]["batches"] == 3
    ops = {i["op"] for i in result["instructions"]}
    assert ops == {"create_gridline", "create_column", "create_wall", "create_beam"}
    assert all(b["instruction_hash"] for b in result["batches"])


def test_plan_skips_unknown_level(config, sample_path):
    ir = _ir(config, sample_path)
    ir.sheet["level"] = "屋面层"
    result = plan(ir, config)
    assert result["stats"]["skipped"] == 6
    assert any(w["code"] == "E-3002" for w in result["warnings"])

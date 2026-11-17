from cad2revit_mcp.models import IR_SCHEMA_VERSION, Element, IRDocument


def test_ir_roundtrip_keeps_schema():
    element = Element("C-001", "column", {"insert": [0, 0], "section": [600, 600]},
                      {"material": "C30"}, {"layer": "KZ"}, 0.95, ["P00001"])
    ir = IRDocument(task_id="t-1", sheet={"level": "1F"}, elements=[element],
                    ambiguous=["C-001"])
    payload = ir.to_dict()
    assert payload["schema_version"] == IR_SCHEMA_VERSION
    restored = IRDocument.from_dict(payload)
    assert restored.elements[0].confidence == 0.95
    assert restored.elements[0].source_entities == ["P00001"]
    assert restored.ambiguous == ["C-001"]

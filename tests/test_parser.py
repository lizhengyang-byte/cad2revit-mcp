from cad2revit_mcp.engine.parser.dxf_parser import parse_dxf


def test_parser_normalizes_units_and_health(config, sample_path):
    parsed = parse_dxf(sample_path, config)
    assert parsed.unit_scale == 1.0
    assert parsed.health["usable"] is True
    assert len(parsed.segments) >= 10
    assert len(parsed.polylines) == 2
    assert any("600x600" in t.content for t in parsed.texts)
    assert parsed.sheets[0]["discipline"] == "structure"


def test_parser_rejects_missing_file(config, tmp_path):
    from cad2revit_mcp.errors import AppError

    with __import__("pytest").raises(AppError) as exc:
        parse_dxf(tmp_path / "nope.dxf", config)
    assert exc.value.code == "E-1002"

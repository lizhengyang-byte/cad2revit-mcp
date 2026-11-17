import pytest

from cad2revit_mcp.errors import AppError


def test_path_outside_workspace_is_rejected(config, tmp_path):
    other = tmp_path.parent / "outside-workspace" / "drawing.dxf"
    with pytest.raises(AppError) as exc:
        config.resolve_path(str(other))
    assert exc.value.code == "E-1001"


def test_relative_path_resolves_inside_workspace(config):
    path = config.resolve_path("samples/a.dxf")
    assert str(config.workspace_dir) in str(path)

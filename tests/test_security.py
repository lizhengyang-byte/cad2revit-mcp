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


def test_sibling_prefix_is_not_inside_workspace(config, tmp_path):
    """/workspace-evil must not pass as a child of /workspace."""
    sibling = tmp_path.parent / (tmp_path.name + "-evil") / "drawing.dxf"
    sibling.parent.mkdir(parents=True, exist_ok=True)
    with pytest.raises(AppError) as exc:
        config.resolve_path(str(sibling))
    assert exc.value.code == "E-1001"


def test_workspace_child_path_is_allowed(config):
    inside = config.workspace_dir / "tasks" / "t-1" / "report.md"
    assert config.is_allowed(inside)

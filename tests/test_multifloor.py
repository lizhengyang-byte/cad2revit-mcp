"""Multi-storey behaviour: side-by-side plans, many files, vertical checks."""

from pathlib import Path

import ezdxf

from cad2revit_mcp.config import Config
from cad2revit_mcp.pipeline import Pipeline
from cad2revit_mcp.samples import (
    PLAN_LAYERS,
    add_plan_block,
    build_floor_file,
    build_multi_floor_drawing,
)


def _pipeline(tmp_path) -> Pipeline:
    return Pipeline(Config(workspace_dir=tmp_path, allowed_paths=[tmp_path]))


def _two_floor_with_shift(tmp_path, shift=(0.0, 0.0)) -> Path:
    path = tmp_path / "shifted.dxf"
    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 4
    for name, color in PLAN_LAYERS.items():
        doc.layers.add(name, color=color)
    msp = doc.modelspace()
    add_plan_block(msp, 0.0, 0.0, "1F 一层平面", elevation="0.000", label_prefix="1F-")
    add_plan_block(msp, 20000.0, 0.0, "2F 二层平面", elevation="3.000",
                   label_prefix="2F-", column_shift=shift)
    doc.saveas(str(path))
    return path


def test_four_floors_in_one_drawing_run_end_to_end(tmp_path):
    pipeline = _pipeline(tmp_path)
    parsed = pipeline.parse(str(build_multi_floor_drawing(tmp_path / "four.dxf", 4)))
    assert len(parsed["regions"]) == 4

    detected = pipeline.detect(parsed["task_id"])
    floors = detected["floors"]
    assert [floor["level"] for floor in floors] == ["1F", "2F", "3F", "4F"]
    assert [floor["elevation_mm"] for floor in floors] == [0.0, 3000.0, 6000.0, 9000.0]
    for floor in floors:
        assert floor["counts"]["by_type"]["grid"] == 4, "每层都要有自己的轴网"
        assert floor["counts"]["by_type"]["column"] == 4

    element_ids = {element["id"] for element in detected["elements"]}
    assert "1F-C-001" in element_ids and "4F-C-001" in element_ids, "跨层构件编号必须唯一"

    cross = [edge for edge in detected["graph"]["edges"] if edge["type"] == "column_stacks_on"]
    assert len(cross) == 12, "四层相同柱网应形成 3×4 条上下层对应关系"

    plan = pipeline.plan(parsed["task_id"])
    assert len(plan["batches"]) == 4
    assert {batch["floor"] for batch in plan["batches"]} == {"1F", "2F", "3F", "4F"}
    levels = {item["level"]: item["action"] for item in plan["levels"]}
    assert levels["4F"] == "create", "未在标高表里的楼层应由指令创建，而不是悄悄落到最近标高"
    assert levels["1F"] == "exists"

    run = pipeline.execute(parsed["task_id"])
    assert run["status"] == "ok"
    check = pipeline.check(parsed["task_id"])
    assert check["metrics"]["missing"] == 0
    assert check["metrics"]["auto_ratio"] == 1.0
    assert check["metrics"]["vertical"] == 0

    report = pipeline.report(parsed["task_id"])
    assert "楼层划分" in report["report"]
    assert "4F" in report["report"]


def test_two_files_one_floor_each(tmp_path):
    pipeline = _pipeline(tmp_path)
    parsed = pipeline.parse("", file_paths=[
        str(build_floor_file(tmp_path / "1F.dxf", 1)),
        str(build_floor_file(tmp_path / "2F.dxf", 2)),
    ])
    detected = pipeline.detect(parsed["task_id"])
    assert [floor["level"] for floor in detected["floors"]] == ["1F", "2F"]
    plan = pipeline.plan(parsed["task_id"])
    assert len(plan["batches"]) == 2
    assert pipeline.execute(parsed["task_id"])["status"] == "ok"


def test_misaligned_columns_are_reported_not_silently_built(tmp_path):
    pipeline = _pipeline(tmp_path)
    parsed = pipeline.parse(str(_two_floor_with_shift(tmp_path, shift=(400.0, 0.0))))
    detected = pipeline.detect(parsed["task_id"])
    kinds = {(issue["kind"], issue["element_id"]) for issue in detected["graph"]["issues"]}
    assert any(kind == "column_misaligned" for kind, _ in kinds), "上下层柱网错位必须被报出"
    ambiguous_kinds = {item["kind"] for item in detected["ambiguous"]}
    assert "column_misaligned" in ambiguous_kinds

    pipeline.plan(parsed["task_id"])
    pipeline.execute(parsed["task_id"])
    check = pipeline.check(parsed["task_id"])
    assert check["metrics"]["vertical"] >= 1
    assert any(item["kind"] == "column_misaligned"
               for item in check["checks"]["vertical"])


def test_duplicate_storey_across_files_is_flagged(tmp_path):
    pipeline = _pipeline(tmp_path)
    parsed = pipeline.parse("", file_paths=[
        str(build_floor_file(tmp_path / "a.dxf", 2)),
        str(build_floor_file(tmp_path / "b.dxf", 2)),
    ])
    detected = pipeline.detect(parsed["task_id"])
    codes = {conflict["code"] for floor in detected["floors"] for conflict in floor["conflicts"]}
    assert "level_duplicate" in codes
    warnings = {item["code"] for item in detected["warnings"]}
    assert "W-1002" in warnings, "重复楼层必须产生告警，不能只写在 conflicts 里"


def test_query_graph_filters_by_floor(tmp_path):
    pipeline = _pipeline(tmp_path)
    parsed = pipeline.parse(str(build_multi_floor_drawing(tmp_path / "four.dxf", 2)))
    pipeline.detect(parsed["task_id"])
    graph = pipeline.query_graph(parsed["task_id"], floor="2F")
    assert graph["floor"] == "2F"
    assert all(node["id"].startswith("2F-") or node["type"] == "grid_intersection"
               for node in graph["nodes"])


def test_resolve_keeps_floor_view_in_sync(tmp_path):
    pipeline = _pipeline(tmp_path)
    parsed = pipeline.parse(str(build_multi_floor_drawing(tmp_path / "four.dxf", 2)))
    detected = pipeline.detect(parsed["task_id"])
    victim = "1F-W-001"
    assert victim in {element["id"] for element in detected["elements"]}
    pipeline.resolve(parsed["task_id"], [{"element_id": victim, "action": "exclude",
                                           "reason": "测试排除"}])
    plan = pipeline.plan(parsed["task_id"])
    assert victim not in {ins["element_id"] for ins in plan["instructions"]}
    floor_1f = next(item for item in plan["floors"] if item["level"] == "1F")
    assert floor_1f["planned"] == 9, "排除后该层计划数量必须同步减少"

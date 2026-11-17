"""Tests for the element graph: the structure the model reasons on."""

from cad2revit_mcp.engine.graph import build_graph


def _detected(pipeline):
    p, task_id = pipeline
    return p.detect(task_id)


def test_graph_anchors_columns_on_axis_intersection(pipeline):
    detected = _detected(pipeline)
    graph = detected["graph"]
    column_edges = [edge for edge in graph["edges"] if edge["type"] == "column_at_intersection"]
    assert column_edges, "柱必须关联到轴网交点"
    assert column_edges[0]["evidence"]["grid_names"] == ["A-1", "A-2"]
    assert column_edges[0]["weight"] <= 250

    nodes = {node["id"]: node for node in graph["nodes"]}
    assert nodes["C-001"]["grid_position"] == "A-1/A-2 @ (0.0, 0.0)"
    assert nodes["GI-001"]["type"] == "grid_intersection"


def test_graph_flags_unsupported_beam_and_isolated_wall(pipeline):
    detected = _detected(pipeline)
    kinds = {(issue["kind"], issue["element_id"]) for issue in detected["graph"]["issues"]}
    assert ("beam_unsupported", "B-001") in kinds
    assert ("isolated_element", "W-001") in kinds

    ambiguous = {(item["element_id"], item["kind"]) for item in detected["ambiguous"]}
    assert ("B-001", "beam_unsupported") in ambiguous, "拓扑异常必须进入疑点清单"
    assert ("W-001", "isolated_element") in ambiguous


def test_graph_summary_is_readable_per_element(pipeline):
    detected = _detected(pipeline)
    summary = detected["graph_summary"]
    assert summary["headline"].count("拓扑异常") == 1
    column_line = next(line for line in summary["lines"] if line.startswith("柱 C-001"))
    assert "轴网交点" in column_line or "交点 A-1/A-2" in column_line
    assert any(line.startswith("梁 B-001") for line in summary["lines"])


def test_subgraph_depth_and_relation_filter(pipeline):
    p, task_id = pipeline
    graph = p.query_graph(task_id, element_id="C-001", depth=1)
    ids = {node["id"] for node in graph["nodes"]}
    assert {"C-001", "GI-001"} <= ids
    assert all("beam_end_supported" != edge["type"] for edge in graph["edges"])

    graph = p.query_graph(task_id, element_id="C-001", depth=2)
    ids = {node["id"] for node in graph["nodes"]}
    assert {"G-001", "G-002", "GI-001"} <= ids, "两跳内应能沿轴网走到另一条轴线"

    graph = p.query_graph(task_id, element_id="C-001", depth=3)
    ids = {node["id"] for node in graph["nodes"]}
    assert "C-002" in ids, "沿轴网三跳应能看到同轴另一根柱"

    graph = p.query_graph(task_id, relation_types=["column_at_intersection"])
    assert len(graph["edges"]) == 1


def test_planner_carries_relations_into_instructions(pipeline):
    p, task_id = pipeline
    plan = p.plan(task_id)
    column = next(item for item in plan["instructions"] if item["element_id"] == "C-001")
    assert any(rel["type"] == "column_at_intersection" for rel in column["relations"])
    beam = next(item for item in plan["instructions"] if item["element_id"] == "B-001")
    assert beam["issues"] == ["beam_unsupported", "isolated_element"]
    assert plan["stats"]["topology_flagged"] == 2


def test_build_graph_is_deterministic(pipeline):
    detected = _detected(pipeline)
    again = build_graph([element for element in detected["elements"]])
    assert again["edges"] == detected["graph"]["edges"]
    assert again["issues"] == detected["graph"]["issues"]

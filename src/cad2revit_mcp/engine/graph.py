"""Element graph: the structure the language model actually reads.

The detector produces a flat list of elements.  A flat list cannot express
"this column sits on the intersection of axes A-1/A-2 and carries two beams",
which is exactly the knowledge an LLM needs before it is allowed to decide
anything.  This module builds that structure:

* **nodes**  - one per element (grid line, column, wall, beam);
* **edges**  - typed, weighted relations between elements;
* **grid intersections** - the anchors columns and walls are judged against;
* **issues** - topological anomalies promoted to ambiguities;
* **summary** - one readable line per element, for prompt context.

Everything is deterministic: same drawing, same graph, same ordering.  The
graph is persisted inside the IR (schema 1.1, field ``graph``) so that every
later stage - planner, verifier, report, LLM - reads the same structure.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple

from ..geometry import angle_of, distance, point_line_distance, project_parameter

GRAPH_SCHEMA = "element-graph/1.0"

# Relation vocabulary.  Adding a type is backward compatible; renaming is not.
REL_TYPES = (
    "column_on_grid",          # column centre lies on an axis line
    "column_at_intersection",  # column centre lies on two axes' crossing
    "wall_follows_grid",       # wall centreline parallel to an axis
    "beam_end_supported",      # beam endpoint lands on a column or wall
    "wall_intersects_column",  # wall passes through a column footprint
    "element_overlap",         # two elements occupy the same space
    "grid_line_at_intersection",  # an axis belongs to a crossing point
    "same_level",              # elements share a storey (reserved)
)

# Issue kinds promoted to ambiguities (resolved through resolve_ambiguities).
ISSUE_KINDS = ("not_on_grid", "beam_unsupported", "element_overlap", "isolated_element")

DEFAULT_GRAPH_RULES = {
    "column_grid_tolerance_mm": 250.0,
    "wall_grid_offset_tolerance_mm": 200.0,
    "beam_end_tolerance_mm": 600.0,
    "overlap_tolerance_mm": 50.0,
}


# --------------------------------------------------------------------------
# small line helpers (kept local: geometry.py stays a pure math module)
# --------------------------------------------------------------------------
Point = Tuple[float, float]


def _segments_intersection(a1: Point, a2: Point, b1: Point, b2: Point,
                           tol: float = 1.0) -> Optional[Point]:
    """Return the crossing point of two segments, or None when they miss.

    Tolerance is applied in millimetres so a 2 mm drafting gap still counts
    as an intersection - that is what makes axis networks usable at all.
    """
    dx1, dy1 = a2[0] - a1[0], a2[1] - a1[1]
    dx2, dy2 = b2[0] - b1[0], b2[1] - b1[1]
    denom = dx1 * dy2 - dy1 * dx2
    if abs(denom) < 1e-9:
        return None
    t = ((b1[0] - a1[0]) * dy2 - (b1[1] - a1[1]) * dx2) / denom
    u = ((b1[0] - a1[0]) * dy1 - (b1[1] - a1[1]) * dx1) / denom
    length_a = max((dx1 * dx1 + dy1 * dy1) ** 0.5, 1.0)
    length_b = max((dx2 * dx2 + dy2 * dy2) ** 0.5, 1.0)
    slack_a = tol / length_a
    slack_b = tol / length_b
    if -slack_a <= t <= 1 + slack_a and -slack_b <= u <= 1 + slack_b:
        return (a1[0] + t * dx1, a1[1] + t * dy1)
    return None


def _grid_lines(elements: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    lines = []
    for element in elements:
        if element["type"] != "grid":
            continue
        geometry = element.get("geometry") or {}
        if "start" not in geometry or "end" not in geometry:
            continue
        lines.append({
            "id": element["id"],
            "name": (element.get("params") or {}).get("name", element["id"]),
            "start": tuple(geometry["start"]),
            "end": tuple(geometry["end"]),
        })
    return lines


def _intersections(grid_lines: List[Dict[str, Any]], tol: float) -> List[Dict[str, Any]]:
    result = []
    index = 0
    for i, first in enumerate(grid_lines):
        for second in grid_lines[i + 1:]:
            point = _segments_intersection(first["start"], first["end"],
                                           second["start"], second["end"], tol)
            if point is None:
                continue
            index += 1
            result.append({
                "id": f"GI-{index:03d}",
                "x": round(point[0], 1),
                "y": round(point[1], 1),
                "grid_lines": [first["id"], second["id"]],
                "grid_names": [first["name"], second["name"]],
            })
    return result


def _nearest_grid(point: Point, grid_lines: List[Dict[str, Any]]) -> Tuple[Optional[Dict[str, Any]], float]:
    best, best_distance = None, float("inf")
    for line in grid_lines:
        dist = point_line_distance(point, line["start"], line["end"])
        t = project_parameter(point, line["start"], line["end"])
        if t < -0.05 or t > 1.05:  # projection outside the axis: not "on" it
            continue
        if dist < best_distance:
            best, best_distance = line, dist
    return best, best_distance


def _nearest_intersection(point: Point, intersections: List[Dict[str, Any]],
                          tol: float) -> Optional[Dict[str, Any]]:
    best, best_distance = None, float("inf")
    for item in intersections:
        dist = distance(point, (item["x"], item["y"]))
        if dist <= tol and dist < best_distance:
            best, best_distance = item, dist
    return best


def _column_section(column: Dict[str, Any]) -> Tuple[float, float]:
    section = (column.get("geometry") or {}).get("section") or [400, 400]
    return float(section[0]), float(section[1])


def _point_of(element: Dict[str, Any]) -> Optional[Point]:
    geometry = element.get("geometry") or {}
    if "insert" in geometry:
        return tuple(geometry["insert"])
    if "start" in geometry:
        return tuple(geometry["start"])
    return None


def _segment_of(element: Dict[str, Any]) -> Optional[Tuple[Point, Point]]:
    geometry = element.get("geometry") or {}
    if "start" in geometry and "end" in geometry:
        return tuple(geometry["start"]), tuple(geometry["end"])
    return None


# --------------------------------------------------------------------------
# graph construction
# --------------------------------------------------------------------------
def build_graph(elements: List[Dict[str, Any]], rules: Optional[Dict[str, Any]] = None,
                issues_as_ambiguities: bool = True) -> Dict[str, Any]:
    """Build the element graph plus the topological issues it exposes."""
    config = {**DEFAULT_GRAPH_RULES, **(rules or {})}
    by_id = {element["id"]: element for element in elements}
    grid_lines = _grid_lines(elements)
    intersections = _intersections(grid_lines, config["overlap_tolerance_mm"])

    nodes = [{
        "id": element["id"],
        "type": element["type"],
        "level": (element.get("params") or {}).get("level")
                  or (element.get("geometry") or {}).get("level"),
        "confidence": element.get("confidence", 0.0),
        "degree": 0,
    } for element in elements]
    nodes.extend({
        "id": item["id"],
        "type": "grid_intersection",
        "level": None,
        "confidence": 1.0,
        "degree": 0,
        "x": item["x"],
        "y": item["y"],
        "grid_names": item["grid_names"],
    } for item in intersections)

    edges: List[Dict[str, Any]] = []
    issues: List[Dict[str, Any]] = []

    def add_edge(source: str, relation: str, target: str, weight: float,
                 unit: str, evidence: Dict[str, Any]) -> None:
        edges.append({
            "id": f"R-{len(edges) + 1:04d}",
            "type": relation,
            "source": source,
            "target": target,
            "weight": round(float(weight), 1),
            "unit": unit,
            "evidence": evidence,
        })

    # ---- axis network topology: which axes form a crossing ----------------
    for item in intersections:
        for grid_id in item["grid_lines"]:
            add_edge(item["id"], "grid_line_at_intersection", grid_id, 0.0, "none",
                     {"grid_names": item["grid_names"], "x": item["x"], "y": item["y"]})

    # ---- columns against the axis network --------------------------------
    for element in elements:
        if element["type"] != "column":
            continue
        point = _point_of(element)
        if point is None:
            continue
        line, offset = _nearest_grid(point, grid_lines)
        if line is not None and offset <= config["column_grid_tolerance_mm"]:
            add_edge(element["id"], "column_on_grid", line["id"], offset, "mm",
                     {"grid_name": line["name"], "offset_mm": round(offset, 1)})
            crossing = _nearest_intersection(point, intersections,
                                             config["column_grid_tolerance_mm"])
            if crossing is not None:
                add_edge(element["id"], "column_at_intersection", crossing["id"],
                         distance(point, (crossing["x"], crossing["y"])), "mm",
                         {"grid_names": crossing["grid_names"],
                          "x": crossing["x"], "y": crossing["y"]})
                node = next(n for n in nodes if n["id"] == element["id"])
                node["grid_position"] = (
                    f"{'/'.join(crossing['grid_names'])} @ ({crossing['x']}, {crossing['y']})"
                )
                continue
        if offset > config["column_grid_tolerance_mm"]:
            issues.append({
                "kind": "not_on_grid",
                "element_id": element["id"],
                "detail": {
                    "reason": "柱中心不在任何轴线上",
                    "nearest_grid": line["name"] if line else None,
                    "offset_mm": round(offset, 1) if line else None,
                    "tolerance_mm": config["column_grid_tolerance_mm"],
                    "position": [round(point[0], 1), round(point[1], 1)],
                },
            })
        elif line is None:
            issues.append({
                "kind": "not_on_grid",
                "element_id": element["id"],
                "detail": {"reason": "图纸未识别到可用轴网",
                           "tolerance_mm": config["column_grid_tolerance_mm"]},
            })

    # ---- walls against the axis network ----------------------------------
    for element in elements:
        if element["type"] != "wall":
            continue
        segment = _segment_of(element)
        if segment is None:
            continue
        start, end = segment
        line, offset = _nearest_grid(start, grid_lines)
        if line is None:
            continue
        angle_diff = abs(angle_of(start, end) - angle_of(line["start"], line["end"]))
        angle_diff = min(angle_diff, 180.0 - angle_diff)
        if angle_diff <= 1.5 and offset <= config["wall_grid_offset_tolerance_mm"]:
            add_edge(element["id"], "wall_follows_grid", line["id"], offset, "mm",
                     {"grid_name": line["name"], "offset_mm": round(offset, 1),
                      "angle_diff_deg": round(angle_diff, 2)})

    # ---- beams, their supports and crossings -----------------------------
    for element in elements:
        if element["type"] != "beam":
            continue
        segment = _segment_of(element)
        if segment is None:
            continue
        start, end = segment
        supported = 0
        for endpoint_index, point in ((0, start), (1, end)):
            best: Optional[Tuple[str, str, float]] = None  # (target_id, target_type, dist)
            for column in elements:
                if column["type"] != "column":
                    continue
                column_point = _point_of(column)
                if column_point is None:
                    continue
                dist = distance(point, column_point)
                limit = config["beam_end_tolerance_mm"] + 0.5 * min(_column_section(column))
                if dist <= limit and (best is None or dist < best[2]):
                    best = (column["id"], "column", dist)
            for wall in elements:
                if wall["type"] != "wall":
                    continue
                wall_segment = _segment_of(wall)
                if wall_segment is None:
                    continue
                t = project_parameter(point, wall_segment[0], wall_segment[1])
                if t < -0.02 or t > 1.02:
                    continue
                dist = point_line_distance(point, wall_segment[0], wall_segment[1])
                tolerance = config["beam_end_tolerance_mm"] * 0.5 + \
                    0.5 * float((wall.get("params") or {}).get("thickness", 200) or 200)
                if dist <= tolerance and (best is None or dist < best[2]):
                    best = (wall["id"], "wall", dist)
            if best is None:
                continue
            supported += 1
            add_edge(element["id"], "beam_end_supported", best[0], best[2], "mm",
                     {"endpoint": "start" if endpoint_index == 0 else "end",
                      "support_type": best[1],
                      "tolerance_mm": config["beam_end_tolerance_mm"]})
        if supported == 0:
            issues.append({
                "kind": "beam_unsupported",
                "element_id": element["id"],
                "detail": {"reason": "梁两端均未落在柱或墙上",
                           "tolerance_mm": config["beam_end_tolerance_mm"]},
            })

        # beam running across a column is a modelling error, not a support
        for column in elements:
            if column["type"] != "column":
                continue
            column_point = _point_of(column)
            if column_point is None:
                continue
            t = project_parameter(column_point, start, end)
            if 0.05 < t < 0.95:
                dist = point_line_distance(column_point, start, end)
                if dist <= 0.5 * min(_column_section(column)) + 10:
                    add_edge(element["id"], "element_overlap", column["id"], dist, "mm",
                             {"reason": "梁中线穿过柱截面"})

    # ---- wall through column --------------------------------------------
    for wall in elements:
        if wall["type"] != "wall":
            continue
        wall_segment = _segment_of(wall)
        if wall_segment is None:
            continue
        thickness = float((wall.get("params") or {}).get("thickness", 200) or 200)
        for column in elements:
            if column["type"] != "column":
                continue
            column_point = _point_of(column)
            if column_point is None:
                continue
            t = project_parameter(column_point, wall_segment[0], wall_segment[1])
            if 0.05 < t < 0.95:
                dist = point_line_distance(column_point, wall_segment[0], wall_segment[1])
                if dist < thickness / 2.0 - config["overlap_tolerance_mm"]:
                    add_edge(wall["id"], "wall_intersects_column", column["id"], dist, "mm",
                             {"reason": "墙中线穿过柱截面内部"})

    # ---- same element type overlaps --------------------------------------
    for i, first in enumerate(elements):
        for second in elements[i + 1:]:
            if first["type"] != second["type"] or first["type"] == "grid":
                continue
            overlap = _same_type_overlap(first, second, config["overlap_tolerance_mm"])
            if overlap is not None:
                add_edge(first["id"], "element_overlap", second["id"], overlap, "mm",
                         {"kind": f"{first['type']}_overlap"})
                issues.append({
                    "kind": "element_overlap",
                    "element_id": first["id"],
                    "detail": {"reason": f"{first['type']} 构件重叠",
                               "other": second["id"], "overlap_mm": round(overlap, 1)},
                })

    level_groups: Dict[str, List[str]] = {}
    for element in elements:
        level = str((element.get("params") or {}).get("level") or
                    (element.get("geometry") or {}).get("level") or "unassigned")
        level_groups.setdefault(level, []).append(element["id"])

    # ---- degrees, isolation, summary ------------------------------------
    degree: Dict[str, int] = {element["id"]: 0 for element in elements}
    for edge in edges:
        if edge["source"] in degree:
            degree[edge["source"]] += 1
        if edge["target"] in degree:
            degree[edge["target"]] += 1
    for node in nodes:
        node["degree"] = degree.get(node["id"], 0)
        if node["degree"] == 0 and node["type"] not in ("grid", "grid_intersection"):
            issues.append({
                "kind": "isolated_element",
                "element_id": node["id"],
                "detail": {"reason": "构件与轴网及其他构件均无关联"},
            })

    summary = _summary(nodes, edges, intersections, issues, by_id)
    metrics = {
        "nodes": len(nodes),
        "edges": len(edges),
        "grid_intersections": len(intersections),
        "isolated": sum(1 for issue in issues if issue["kind"] == "isolated_element"),
        "issues": len(issues),
        "by_relation": {rel: sum(1 for edge in edges if edge["type"] == rel) for rel in REL_TYPES},
        "by_level": {level: len(ids) for level, ids in level_groups.items()},
    }
    return {
        "schema": GRAPH_SCHEMA,
        "nodes": nodes,
        "edges": edges,
        "grid_intersections": intersections,
        "issues": issues,
        "metrics": metrics,
        "summary": summary,
        "ambiguities": [
            {"element_id": issue["element_id"], "kind": issue["kind"],
             "detail": issue["detail"]}
            for issue in issues
        ] if issues_as_ambiguities else [],
    }


def _same_type_overlap(first: Dict[str, Any], second: Dict[str, Any],
                       tol: float) -> Optional[float]:
    """Return an overlap measure for two same-type elements, else None."""
    if first["type"] == "column":
        a = _point_of(first)
        b = _point_of(second)
        if a and b:
            gap = distance(a, b)
            limit = 0.5 * min(min(_column_section(first)), min(_column_section(second)))
            if gap < limit - tol:
                return limit - gap
        return None
    if first["type"] in ("wall", "beam"):
        a = _segment_of(first)
        b = _segment_of(second)
        if not a or not b:
            return None
        # nearly identical centreline = duplicated element
        if distance(a[0], b[0]) <= tol * 4 and distance(a[1], b[1]) <= tol * 4:
            return distance(a[0], a[1])
        if distance(a[0], b[1]) <= tol * 4 and distance(a[1], b[0]) <= tol * 4:
            return distance(a[0], a[1])
        return None
    return None


def _summary(nodes: List[Dict[str, Any]], edges: List[Dict[str, Any]],
             intersections: List[Dict[str, Any]], issues: List[Dict[str, Any]],
             by_id: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Human and model readable summary.

    ``lines`` is ordered by element id so prompt caches stay stable; each line
    names the element, its anchors and the relations an LLM must respect.
    """
    neighbours: Dict[str, List[str]] = {}
    for edge in edges:
        if edge["type"] == "same_level":
            continue
        label = _edge_label(edge, by_id)
        neighbours.setdefault(edge["source"], []).append(label)
        neighbours.setdefault(edge["target"], []).append(label)

    issues_by_element: Dict[str, List[str]] = {}
    for issue in issues:
        issues_by_element.setdefault(issue["element_id"], []).append(issue["kind"])

    lines = []
    for node in nodes:
        if node["type"] == "grid_intersection":
            continue
        element_id = node["id"]
        element = by_id.get(element_id, {})
        title = {
            "grid": "轴线",
            "column": "柱",
            "wall": "墙",
            "beam": "梁",
        }.get(element["type"], element["type"])
        anchor = f"，锚点 {node['grid_position']}" if node.get("grid_position") else ""
        neighbours_text = "；".join(neighbours.get(element_id, [])) or "无关联"
        issue_text = ""
        if issues_by_element.get(element_id):
            issue_text = f"；异常 {', '.join(issues_by_element[element_id])}"
        lines.append(
            f"{title} {element_id}{anchor}，置信度 {node['confidence']:.2f}，"
            f"关系：{neighbours_text}{issue_text}"
        )

    headline_parts = [f"{len(nodes)} 个构件", f"{len(edges)} 条关系"]
    if intersections:
        headline_parts.append(f"{len(intersections)} 个轴网交点")
    if issues:
        headline_parts.append(f"{len(issues)} 个拓扑异常")
    headline = "，".join(headline_parts)

    return {
        "headline": headline,
        "lines": lines,
        "intersections": [
            f"{item['id']} {('/'.join(item['grid_names']))} ({item['x']}, {item['y']})"
            for item in intersections
        ],
        "issue_lines": [
            f"{issue['kind']}: {issue['element_id']} - {issue['detail'].get('reason', '')}"
            for issue in issues
        ],
    }


def _edge_label(edge: Dict[str, Any], by_id: Dict[str, Dict[str, Any]]) -> str:
    relation = edge["type"]
    if relation == "column_on_grid":
        return f"位于轴线 {edge['evidence'].get('grid_name')}（偏移 {edge['weight']} mm）"
    if relation == "column_at_intersection":
        return f"位于交点 {'/'.join(edge['evidence'].get('grid_names', []))}"
    if relation == "wall_follows_grid":
        return f"沿轴线 {edge['evidence'].get('grid_name')}（偏移 {edge['weight']} mm）"
    if relation == "beam_end_supported":
        target = by_id.get(edge["target"], {})
        kind = "柱" if target.get("type") == "column" else "墙"
        endpoint = "起点" if edge["evidence"].get("endpoint") == "start" else "终点"
        return f"{endpoint}支座 {kind} {edge['target']}（距离 {edge['weight']} mm）"
    if relation == "element_overlap":
        return f"与 {edge['target']} 重叠（{edge['evidence'].get('reason', '')}）"
    if relation == "wall_intersects_column":
        return f"穿过柱 {edge['target']}"
    return relation


def subgraph(graph: Dict[str, Any], element_id: Optional[str] = None,
             relation_types: Optional[List[str]] = None,
             depth: int = 1) -> Dict[str, Any]:
    """Return a neighbourhood slice of the graph, for ``query_graph``."""
    allowed = set(relation_types) if relation_types else None
    edges = [edge for edge in graph.get("edges", [])
             if allowed is None or edge["type"] in allowed]

    if element_id is None:
        nodes = graph.get("nodes", [])
    else:
        visited = {element_id}
        frontier = {element_id}
        for _ in range(max(1, depth)):
            next_frontier = set()
            for edge in edges:
                if edge["source"] in frontier and edge["target"] not in visited:
                    next_frontier.add(edge["target"])
                elif edge["target"] in frontier and edge["source"] not in visited:
                    next_frontier.add(edge["source"])
            visited |= next_frontier
            frontier = next_frontier
        nodes = [node for node in graph.get("nodes", []) if node["id"] in visited]

    node_ids = {node["id"] for node in nodes}
    edges = [edge for edge in edges
             if edge["source"] in node_ids and edge["target"] in node_ids]
    issues = [issue for issue in graph.get("issues", [])
              if issue["element_id"] in node_ids]
    return {
        "schema": graph.get("schema", GRAPH_SCHEMA),
        "nodes": nodes,
        "edges": edges,
        "issues": issues,
        "summary": {
            "headline": f"{len(nodes)} 个构件，{len(edges)} 条关系",
            "lines": [line for line in graph.get("summary", {}).get("lines", [])
                      if any(
                          node["id"] in line.split("，")[0].split(" ")
                          for node in nodes
                          if node["type"] != "grid_intersection"
                      )],
        },
    }

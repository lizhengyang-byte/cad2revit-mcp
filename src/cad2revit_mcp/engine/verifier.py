"""Post-build verification against the original drawing and the IR."""

from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List

from ..geometry import distance
from ..models import Element, IRDocument, required_params


def _position_of(element: Element):
    geometry = element.geometry
    if "insert" in geometry:
        return tuple(geometry["insert"])
    if "start" in geometry:
        return tuple(geometry["start"])
    return None


def verify(ir: IRDocument, execution: Dict[str, Any], tolerance_mm: float = 50.0,
           checks: List[str] | None = None) -> Dict[str, Any]:
    enabled = checks or ["missing", "offset", "param", "duplicate", "topology"]
    created = execution.get("created", [])
    created_ids = [item["element_id"] for item in created]
    created_set = set(created_ids)

    result: Dict[str, List[Dict[str, Any]]] = {
        "missing": [], "offset": [], "param": [], "duplicate": [], "topology": [],
    }

    if "missing" in enabled:
        for element in ir.elements:
            if element.id not in created_set:
                result["missing"].append({
                    "element_id": element.id,
                    "type": element.type,
                    "reason": "未在模型中创建",
                })

    if "duplicate" in enabled:
        for element_id, count in Counter(created_ids).items():
            if count > 1:
                result["duplicate"].append({"element_id": element_id, "count": count})

    if "param" in enabled:
        for element in ir.elements:
            missing = [name for name in required_params(element.type)
                       if name not in element.params and name not in element.geometry]
            if missing:
                result["param"].append({
                    "element_id": element.id,
                    "type": element.type,
                    "missing": missing,
                })

    if "offset" in enabled:
        placed = {item["element_id"]: item for item in created if item.get("position")}
        for element in ir.elements:
            item = placed.get(element.id)
            if not item:
                continue
            expected = _position_of(element)
            if not expected:
                continue
            actual = tuple(item["position"])
            deviation = distance(expected, actual)
            if deviation > tolerance_mm:
                result["offset"].append({
                    "element_id": element.id,
                    "deviation_mm": round(deviation, 1),
                })

    if "topology" in enabled:
        graph = ir.graph or {}
        created_by_id = {item["element_id"]: item for item in created}
        for issue in graph.get("issues", []):
            element_id = issue.get("element_id")
            landed = element_id in created_by_id
            result["topology"].append({
                "element_id": element_id,
                "kind": issue.get("kind"),
                "reason": (issue.get("detail") or {}).get("reason", ""),
                "created": landed,
                "position": created_by_id.get(element_id, {}).get("position"),
            })

    total = len(ir.elements)
    problem_count = sum(len(v) for v in result.values())
    metrics = {
        "tolerance_mm": tolerance_mm,
        "elements": total,
        "created": len(created),
        "auto_ratio": round(len(created_set) / total, 2) if total else 0.0,
        "problems": problem_count,
        "missing": len(result["missing"]),
        "offset": len(result["offset"]),
        "param": len(result["param"]),
        "duplicate": len(result["duplicate"]),
        "topology": len(result["topology"]),
    }
    return {"checks": result, "metrics": metrics}

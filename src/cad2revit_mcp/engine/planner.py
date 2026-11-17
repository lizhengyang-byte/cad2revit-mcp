"""Turn the IR into a deterministic instruction set for Revit.

The planner is also the last place where the *structure* of the drawing is
still visible: every instruction carries the graph relations that justified
it (``relations``), so the add-in and the language model can both check why
an element is being created where it is.

Storeys are the batching unit.  A batch never mixes two storeys, so a failure
rolls back exactly one floor and never leaves a half-built neighbour behind.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List

from ..errors import (
    E_LEVEL_UNKNOWN,
    E_MAPPING_MISSING,
    W_LEVEL_FALLBACK,
    W_PARAM_MISSING,
    W_TOPOLOGY,
    warning,
)
from ..models import Element, IRDocument, required_params

OP_BY_TYPE = {
    "grid": "create_gridline",
    "column": "create_column",
    "wall": "create_wall",
    "beam": "create_beam",
}


def _hash_instructions(instructions: List[Dict[str, Any]]) -> str:
    payload = json.dumps(instructions, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _relations_for(element_id: str, ir: IRDocument) -> List[Dict[str, Any]]:
    """Flatten the graph edges touching this element into a short list."""
    graph = ir.graph or {}
    result = []
    for edge in graph.get("edges", []):
        if edge.get("type") == "same_level":
            continue
        if edge.get("source") == element_id:
            other = edge.get("target")
        elif edge.get("target") == element_id:
            other = edge.get("source")
        else:
            continue
        result.append({
            "type": edge.get("type"),
            "other": other,
            "weight": edge.get("weight"),
            "evidence": edge.get("evidence", {}),
        })
    return result


def _issue_kinds(element_id: str, ir: IRDocument) -> List[str]:
    graph = ir.graph or {}
    return [issue["kind"] for issue in graph.get("issues", [])
            if issue.get("element_id") == element_id]


def _floor_units(ir: IRDocument) -> List[Dict[str, Any]]:
    """Storeys to plan for; a single-sheet IR degrades to one unnamed storey."""
    if ir.floors:
        return ir.floors
    return [{
        "level": ir.sheet.get("level") or "",
        "elevation_mm": None,
        "elements": [element.to_dict() for element in ir.elements],
        "conflicts": [],
    }]


def plan(ir: IRDocument, config, level_map: Dict[str, float] | None = None,
         family_map: Dict[str, Dict[str, str]] | None = None,
         batch_size: int | None = None) -> Dict[str, Any]:
    levels = {**config.level_map, **(level_map or {})}
    families = {**config.family_map, **(family_map or {})}
    size = int(batch_size or config.batch_size)

    instructions: List[Dict[str, Any]] = []
    level_instructions: List[Dict[str, Any]] = []
    warnings: List[Dict[str, str]] = []
    skipped: List[Dict[str, Any]] = []
    floor_reports: List[Dict[str, Any]] = []

    for floor in _floor_units(ir):
        raw_level = str(floor.get("level") or "")
        level = raw_level
        if not level:
            level = getattr(config, "default_level", "1F")
            if level:
                warnings.append(warning(W_LEVEL_FALLBACK,
                                        f"{floor.get('region_id', '平面')}: 未标注楼层，按 {level} 处理"))
        detected_elevation = floor.get("elevation_mm")
        needs_level = False
        if level not in levels:
            if detected_elevation is not None:
                levels[level] = float(detected_elevation)
                needs_level = True
            else:
                warnings.append(warning(E_LEVEL_UNKNOWN,
                                        f"标高 {level} 不在映射表且图纸未标注，该层构件已跳过"))
                for element in floor.get("elements", []):
                    skipped.append({"element_id": element["id"], "code": E_LEVEL_UNKNOWN,
                                    "message": f"level {level} unknown"})
                floor_reports.append({"level": level, "elevation_mm": None, "planned": 0,
                                      "skipped": len(floor.get("elements", [])),
                                      "create_level": False, "status": "skipped"})
                continue

        floor_instructions: List[Dict[str, Any]] = []
        for raw in floor.get("elements", []):
            element = Element.from_dict(raw)
            op = OP_BY_TYPE.get(element.type)
            if not op:
                skipped.append({"element_id": element.id, "code": E_MAPPING_MISSING,
                                "message": f"unsupported element type: {element.type}"})
                continue

            mapping = families.get(element.type) or {}
            if not mapping.get("family") or not mapping.get("type"):
                warnings.append(warning(E_MAPPING_MISSING, f"{element.id}: 缺少族类型映射，已跳过"))
                skipped.append({"element_id": element.id, "code": E_MAPPING_MISSING,
                                "message": "family mapping missing"})
                continue

            missing = [p for p in required_params(element.type)
                       if p != "level" and p not in element.params and p not in element.geometry]
            if missing:
                warnings.append(warning(W_PARAM_MISSING, f"{element.id}: 缺少参数 {', '.join(missing)}"))

            kinds = _issue_kinds(element.id, ir)
            if kinds:
                warnings.append(warning(W_TOPOLOGY,
                                        f"{element.id}: 拓扑异常 {', '.join(kinds)}，已按图面位置建模，请复核"))

            floor_instructions.append({
                "element_id": element.id,
                "op": op,
                "level": level,
                "floor": floor.get("floor_key") or level,
                "elevation_mm": levels[level],
                "family": mapping["family"],
                "type": mapping["type"],
                "geometry": element.geometry,
                "params": element.params,
                "relations": _relations_for(element.id, ir),
                "issues": kinds,
            })

        if needs_level:
            level_instructions.append({
                "element_id": f"LEVEL:{level}",
                "op": "create_level",
                "level": level,
                "floor": floor.get("floor_key") or level,
                "elevation_mm": levels[level],
                "params": {"name": level, "elevation_mm": levels[level]},
                "geometry": {},
                "relations": [],
                "issues": [],
            })

        instructions.extend(floor_instructions)
        floor_reports.append({
            "level": level,
            "elevation_mm": levels[level],
            "region_id": floor.get("region_id"),
            "planned": len(floor_instructions),
            "skipped": len(floor.get("elements", [])) - len(floor_instructions),
            "create_level": needs_level,
        })

    # batches: one storey per batch, never mixed
    batches = []
    counter = 1
    for report in floor_reports:
        level = report["level"]
        pool = [ins for ins in level_instructions if ins["level"] == level]
        pool += [ins for ins in instructions if ins["level"] == level]
        for start in range(0, len(pool), size):
            chunk = pool[start:start + size]
            batches.append({
                "batch_no": counter,
                "floor": level,
                "level": level,
                "elevation_mm": levels.get(level),
                "instruction_hash": _hash_instructions(chunk),
                "count": len(chunk),
                "instructions": chunk,
            })
            counter += 1

    return {
        "instructions": [*level_instructions, *instructions],
        "batches": batches,
        "levels": [{"level": report["level"], "elevation_mm": report["elevation_mm"],
                    "action": ("skipped" if report.get("status") == "skipped" else
                               ("create" if report.get("create_level") else "exists"))}
                   for report in floor_reports],
        "floors": floor_reports,
        "batch_size": size,
        "warnings": warnings,
        "skipped": skipped,
        "stats": {
            "elements": sum(len(floor.get("elements", [])) for floor in _floor_units(ir)),
            "planned": len(instructions),
            "skipped": len(skipped),
            "batches": len(batches),
            "floors": len(floor_reports),
            "levels_created": len(level_instructions),
            "topology_flagged": sum(1 for item in instructions if item["issues"]),
        },
    }

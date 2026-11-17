"""Turn the IR into a deterministic instruction set for Revit.

The planner is also the last place where the *structure* of the drawing is
still visible: every instruction carries the graph relations that justified
it (``relations``), so the add-in and the language model can both check why
an element is being created where it is.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional

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


def _level_of(element: Element, ir: IRDocument) -> Optional[str]:
    level = element.params.get("level") or ir.sheet.get("level") or ""
    return str(level) if level else None


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


def plan(ir: IRDocument, config, level_map: Dict[str, float] | None = None,
         family_map: Dict[str, Dict[str, str]] | None = None,
         batch_size: int | None = None) -> Dict[str, Any]:
    levels = {**config.level_map, **(level_map or {})}
    families = {**config.family_map, **(family_map or {})}
    size = int(batch_size or config.batch_size)

    instructions: List[Dict[str, Any]] = []
    warnings: List[Dict[str, str]] = []
    skipped: List[Dict[str, Any]] = []

    for element in ir.elements:
        op = OP_BY_TYPE.get(element.type)
        if not op:
            skipped.append({"element_id": element.id, "code": E_MAPPING_MISSING,
                            "message": f"unsupported element type: {element.type}"})
            continue

        mapping = families.get(element.type) or {}
        if not mapping.get("family") or not mapping.get("type"):
            warnings.append(warning(E_MAPPING_MISSING,
                                    f"{element.id}: 缺少族类型映射，已跳过"))
            skipped.append({"element_id": element.id, "code": E_MAPPING_MISSING,
                            "message": "family mapping missing"})
            continue

        level = _level_of(element, ir)
        if not level:
            level = getattr(config, "default_level", "1F")
            if level:
                warnings.append(warning(W_LEVEL_FALLBACK,
                                        f"{element.id}: 图纸未标注楼层，按 {level} 处理"))
        if not level:
            warnings.append(warning(E_LEVEL_UNKNOWN, f"{element.id}: 未指定楼层标高，已跳过"))
            skipped.append({"element_id": element.id, "code": E_LEVEL_UNKNOWN,
                            "message": "level unknown"})
            continue
        if level not in levels:
            warnings.append(warning(E_LEVEL_UNKNOWN, f"{element.id}: 标高 {level} 不在映射表，已跳过"))
            skipped.append({"element_id": element.id, "code": E_LEVEL_UNKNOWN,
                            "message": f"level {level} not in level map"})
            continue

        missing = [p for p in required_params(element.type)
                   if p != "level" and p not in element.params and p not in element.geometry]
        if missing:
            warnings.append(warning(W_PARAM_MISSING,
                                    f"{element.id}: 缺少参数 {', '.join(missing)}"))

        kinds = _issue_kinds(element.id, ir)
        if kinds:
            warnings.append(warning(W_TOPOLOGY,
                                    f"{element.id}: 拓扑异常 {', '.join(kinds)}，已按图面位置建模，请复核"))

        instructions.append({
            "element_id": element.id,
            "op": op,
            "level": level,
            "elevation_mm": levels[level],
            "family": mapping["family"],
            "type": mapping["type"],
            "geometry": element.geometry,
            "params": element.params,
            "relations": _relations_for(element.id, ir),
            "issues": kinds,
        })

    batches = []
    for i in range(0, len(instructions), size):
        chunk = instructions[i:i + size]
        batches.append({
            "batch_no": i // size + 1,
            "instruction_hash": _hash_instructions(chunk),
            "count": len(chunk),
            "instructions": chunk,
        })

    return {
        "instructions": instructions,
        "batches": batches,
        "batch_size": size,
        "warnings": warnings,
        "skipped": skipped,
        "stats": {
            "elements": len(ir.elements),
            "planned": len(instructions),
            "skipped": len(skipped),
            "batches": len(batches),
            "topology_flagged": sum(1 for item in instructions if item["issues"]),
        },
    }

"""Ambiguity resolution: apply human or model decisions to the IR."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Tuple

from ..models import Element, IRDocument

VALID_ACTIONS = ("accept", "modify", "exclude")


def resolve(ir: IRDocument, decisions: List[Dict[str, Any]], apply_to_similar: bool = False,
            decided_by: str = "llm") -> Tuple[IRDocument, List[Dict[str, Any]], Dict[str, Any]]:
    """Apply decisions and return the updated IR, conflicts and a summary.

    A decision never touches elements that were not named in it; this is the
    hard rule from the API specification ("修一个改一片" is not allowed).
    """
    conflicts: List[Dict[str, Any]] = []
    applied: List[Dict[str, Any]] = []
    excluded: List[str] = []
    by_id = {element.id: element for element in ir.elements}

    for decision in decisions:
        element_id = decision.get("element_id")
        action = decision.get("action")
        if element_id not in by_id:
            conflicts.append({"element_id": element_id, "reason": "unknown element"})
            continue
        if action not in VALID_ACTIONS:
            conflicts.append({"element_id": element_id, "reason": f"invalid action: {action}"})
            continue

        element = by_id[element_id]
        if action == "exclude":
            excluded.append(element_id)
            applied.append({"element_id": element_id, "action": action})
            continue

        params = decision.get("params") or {}
        if params:
            merged = dict(element.params)
            merged.update(params)
            element.params = merged
        geometry_patch = decision.get("geometry") or {}
        if geometry_patch:
            merged_geometry = dict(element.geometry)
            merged_geometry.update(geometry_patch)
            element.geometry = merged_geometry

        if action in ("accept", "modify"):
            if element.confidence < 0.9:
                element.confidence = 0.9
            element.evidence = {**element.evidence, "decision": action,
                                "decided_by": decided_by,
                                "decided_at": datetime.now().isoformat(timespec="seconds")}
        applied.append({"element_id": element_id, "action": action,
                        "params": params, "reason": decision.get("reason", "")})

        if apply_to_similar:
            for other in ir.elements:
                if other.id != element_id and other.id in ir.ambiguous:
                    other.params = {**other.params, **params}
                    other.evidence = {**other.evidence, "decision": "accept_similar"}

    if excluded:
        ir.elements = [e for e in ir.elements if e.id not in excluded]
    ir.ambiguous = [element_id for element_id in ir.ambiguous
                    if element_id not in excluded
                    and element_id in {e.id for e in ir.elements}
                    and by_id.get(element_id, Element("x", "grid", {})).confidence < 0.9]

    summary = {
        "applied": len(applied),
        "excluded": len(excluded),
        "conflicts": len(conflicts),
        "decisions": applied,
    }
    return ir, conflicts, summary

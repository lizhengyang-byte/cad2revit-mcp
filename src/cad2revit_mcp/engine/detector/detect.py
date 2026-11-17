"""Deterministic rule based recognition.

Four element types are supported in the MVP: grid axes, columns, walls and
beams.  Every element carries the evidence that produced it plus a
confidence score built from fixed weights, so a user can always see *why*
the tool believes something is a column.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from ...geometry import (
    bbox,
    bbox_size,
    distance,
    overlap_length,
    point_line_distance,
    project_parameter,
    rectangle_from_points,
)
from ...models import Element, ParsedDrawing, TextItem
from .rules import match_layer, nearest_text, parse_section, section_label_in


def _confidence(rules: Dict[str, Any], layer_hit: bool, geometry_ok: bool,
                text_hit: bool, conflict: bool = False) -> float:
    weights = rules.get("confidence", {})
    score = 0.0
    if layer_hit:
        score += weights.get("layer_weight", 0.5)
    if geometry_ok:
        score += weights.get("geometry_weight", 0.3)
    if text_hit:
        score += weights.get("text_weight", 0.2)
    if conflict:
        score -= weights.get("conflict_penalty", 0.25)
    return max(0.0, min(1.0, round(score, 2)))


def _texts_near(texts: List[TextItem], point: Tuple[float, float], radius: float) -> List[TextItem]:
    result = []
    for text in texts:
        if distance(text.insert, point) <= radius:
            result.append(text)
    return result


def detect_grids(parsed: ParsedDrawing, rules: Dict[str, Any]) -> List[Element]:
    patterns = rules["layers"].get("grid", [])
    if not parsed.segments:
        return []
    all_points = [p for s in parsed.segments for p in (s.start, s.end)]
    x0, y0, x1, y1 = bbox(all_points)
    span = max(x1 - x0, y1 - y0)
    min_length = span * float(rules.get("grid", {}).get("min_length_ratio", 0.4))
    elements = []
    index = 0
    for segment in parsed.segments:
        if not match_layer(segment.layer, patterns):
            continue
        if segment.length < min_length:
            continue
        index += 1
        element_id = f"G-{index:03d}"
        name = f"A-{index}"
        elements.append(Element(
            id=element_id,
            type="grid",
            geometry={"start": list(segment.start), "end": list(segment.end)},
            params={"name": name},
            evidence={"layer": segment.layer, "source": segment.id},
            confidence=_confidence(rules, layer_hit=True, geometry_ok=True, text_hit=False),
            source_entities=[segment.id],
        ))
    return elements


def detect_columns(parsed: ParsedDrawing, rules: Dict[str, Any]) -> Tuple[List[Element], List[Dict[str, Any]]]:
    patterns = rules["layers"].get("column", [])
    cfg = rules.get("column", {})
    min_section = float(cfg.get("min_section", 200))
    max_section = float(cfg.get("max_section", 1500))
    default_section = cfg.get("default_section", [400, 400])

    elements: List[Element] = []
    ambiguities: List[Dict[str, Any]] = []
    used_points: List[Tuple[float, float]] = []
    index = 0

    def make(insert, section, layer, source, geometry_ok, text, conflict, extra_evidence=None):
        nonlocal index
        index += 1
        element_id = f"C-{index:03d}"
        layer_hit = bool(layer) and match_layer(layer, patterns)
        evidence = {"layer": layer, "source": source}
        if text:
            evidence["text"] = text.content
        if extra_evidence:
            evidence.update(extra_evidence)
        confidence = _confidence(rules, layer_hit=layer_hit, geometry_ok=geometry_ok,
                                 text_hit=bool(text), conflict=conflict)
        element = Element(
            id=element_id,
            type="column",
            geometry={"insert": [round(insert[0], 1), round(insert[1], 1)],
                      "section": [round(section[0], 1), round(section[1], 1)],
                      "rotation": 0},
            params={"material": "C30"},
            evidence=evidence,
            confidence=confidence,
            source_entities=[source],
        )
        if conflict:
            ambiguities.append({
                "element_id": element_id,
                "kind": "section_conflict",
                "detail": {"reason": "标注与几何尺寸不一致", "text": evidence.get("text"),
                           "geometric_section": [section[0], section[1]]},
            })
        return element

    for polyline in parsed.polylines:
        if not polyline.get("closed"):
            continue
        layer = polyline.get("layer", "")
        if not match_layer(layer, patterns):
            continue
        points = [tuple(p) for p in polyline["points"]]
        w, h = bbox_size(points)
        if not (min_section <= min(w, h) and max(w, h) <= max_section):
            continue
        center = ((bbox(points)[0] + bbox(points)[2]) / 2,
                  (bbox(points)[1] + bbox(points)[3]) / 2)
        rect_side = rectangle_from_points(points)
        geometry_ok = rect_side is not None
        text = nearest_text(parsed.texts, center, max(w, h) * 2.0)
        section = (w, h)
        conflict = False
        if text:
            labeled = section_label_in(text.content)
            if labeled:
                if abs(labeled[0] - min(w, h)) > 50 or abs(labeled[1] - max(w, h)) > 50:
                    conflict = True
                    section = labeled
        elements.append(make(center, section, layer, polyline["id"], geometry_ok, text, conflict))
        used_points.append(center)

    for block in parsed.blocks:
        layer = block.get("layer", "")
        name = block.get("name", "")
        if not (match_layer(layer, patterns) or match_layer(name, patterns)):
            continue
        insert = tuple(block["insert"])
        if any(distance(insert, p) <= 100 for p in used_points):
            continue
        section = tuple(default_section)
        text = nearest_text(parsed.texts, insert, 1200)
        if text:
            labeled = section_label_in(text.content)
            if labeled:
                section = labeled
        elements.append(make(insert, section, layer, block["id"], geometry_ok=False,
                             text=text, conflict=False,
                             extra_evidence={"block": name}))
        used_points.append(insert)
        if text is None:
            ambiguities.append({
                "element_id": elements[-1].id,
                "kind": "section_missing",
                "detail": {"reason": "柱截面取自默认值，缺少标注依据"},
            })

    return elements, ambiguities


def detect_walls(parsed: ParsedDrawing, rules: Dict[str, Any]) -> Tuple[List[Element], List[Dict[str, Any]]]:
    patterns = rules["layers"].get("wall", [])
    cfg = rules.get("wall", {})
    min_t = float(cfg.get("min_thickness", 60))
    max_t = float(cfg.get("max_thickness", 400))
    min_len = float(cfg.get("min_length", 300))
    overlap_ratio = float(cfg.get("overlap_ratio", 0.6))

    candidates = [s for s in parsed.segments
                  if match_layer(s.layer, patterns) and s.length >= min_len]
    elements: List[Element] = []
    ambiguities: List[Dict[str, Any]] = []
    used: set[str] = set()
    index = 0

    for i, a in enumerate(candidates):
        if a.id in used:
            continue
        for b in candidates[i + 1:]:
            if b.id in used:
                continue
            if not _parallel(a, b):
                continue
            thickness = point_line_distance(b.start, a.start, a.end)
            if not (min_t <= thickness <= max_t):
                continue
            overlap = overlap_length(a.start, a.end, b.start, b.end)
            if overlap < min_len or overlap < overlap_ratio * min(a.length, b.length):
                continue
            start, end = _centerline(a, b, thickness)
            index += 1
            element = Element(
                id=f"W-{index:03d}",
                type="wall",
                geometry={
                    "start": [round(start[0], 1), round(start[1], 1)],
                    "end": [round(end[0], 1), round(end[1], 1)],
                    "thickness": round(thickness, 1),
                },
                params={"thickness": round(thickness, 1), "material": "混凝土"},
                evidence={"layer": a.layer, "source": f"{a.id},{b.id}", "pair_distance": round(thickness, 1)},
                confidence=_confidence(rules, layer_hit=True, geometry_ok=True, text_hit=False),
                source_entities=[a.id, b.id],
            )
            elements.append(element)
            used.update({a.id, b.id})
            break

    return elements, ambiguities


def _parallel(a, b, tol_deg: float = 1.5) -> bool:
    import math

    dx1, dy1 = a.end[0] - a.start[0], a.end[1] - a.start[1]
    dx2, dy2 = b.end[0] - b.start[0], b.end[1] - b.start[1]
    len1 = (dx1 * dx1 + dy1 * dy1) ** 0.5
    len2 = (dx2 * dx2 + dy2 * dy2) ** 0.5
    if len1 == 0 or len2 == 0:
        return False
    cross = abs(dx1 * dy2 - dy1 * dx2) / (len1 * len2)
    angle = math.degrees(math.asin(min(1.0, cross)))
    return angle <= tol_deg


def _centerline(a, b, thickness):
    dx, dy = a.end[0] - a.start[0], a.end[1] - a.start[1]
    length = (dx * dx + dy * dy) ** 0.5
    ux, uy = dx / length, dy / length
    nx, ny = -uy, ux
    side = 1.0 if (b.start[0] - a.start[0]) * nx + (b.start[1] - a.start[1]) * ny >= 0 else -1.0
    t1 = project_parameter(b.start, a.start, a.end)
    t2 = project_parameter(b.end, a.start, a.end)
    lo, hi = max(0.0, min(t1, t2)), min(1.0, max(t1, t2))
    offset = thickness / 2.0 * side
    start = (a.start[0] + ux * lo * length + nx * offset,
             a.start[1] + uy * lo * length + ny * offset)
    end = (a.start[0] + ux * hi * length + nx * offset,
           a.start[1] + uy * hi * length + ny * offset)
    return start, end


def detect_beams(parsed: ParsedDrawing, rules: Dict[str, Any]) -> Tuple[List[Element], List[Dict[str, Any]]]:
    patterns = rules["layers"].get("beam", [])
    cfg = rules.get("beam", {})
    min_len = float(cfg.get("min_length", 1000))
    default_section = cfg.get("default_section", [250, 500])
    label_pattern = cfg.get("label_pattern", "")

    beam_segments = [s for s in parsed.segments
                     if match_layer(s.layer, patterns) and s.length >= min_len]
    elements: List[Element] = []
    ambiguities: List[Dict[str, Any]] = []
    used_segments: set[str] = set()
    index = 0

    for text in parsed.texts:
        labeled = parse_section(text.content, label_pattern) if label_pattern else None
        if not labeled:
            continue
        nearest = None
        nearest_distance = 800.0
        for segment in beam_segments:
            if segment.id in used_segments:
                continue
            dist = point_line_distance(text.insert, segment.start, segment.end)
            if dist <= nearest_distance:
                nearest, nearest_distance = segment, dist
        index += 1
        if nearest is None:
            elements.append(Element(
                id=f"B-{index:03d}",
                type="beam",
                geometry={"start": [round(text.insert[0], 1), round(text.insert[1], 1)],
                          "end": [round(text.insert[0] + 6000, 1), round(text.insert[1], 1)],
                          "width": labeled[0], "depth": labeled[1]},
                params={"width": labeled[0], "depth": labeled[1]},
                evidence={"layer": "", "text": text.content},
                confidence=_confidence(rules, layer_hit=False, geometry_ok=False, text_hit=True),
                source_entities=[text.id],
            ))
            ambiguities.append({
                "element_id": elements[-1].id,
                "kind": "no_geometry",
                "detail": {"reason": "只有梁标注，未找到对应梁线", "text": text.content},
            })
            continue
        used_segments.add(nearest.id)
        elements.append(Element(
            id=f"B-{index:03d}",
            type="beam",
            geometry={"start": list(nearest.start), "end": list(nearest.end),
                      "width": labeled[0], "depth": labeled[1]},
            params={"width": labeled[0], "depth": labeled[1], "material": "C30"},
            evidence={"layer": nearest.layer, "text": text.content},
            confidence=_confidence(rules, layer_hit=True, geometry_ok=True, text_hit=True),
            source_entities=[nearest.id, text.id],
        ))

    for segment in beam_segments:
        if segment.id in used_segments:
            continue
        index += 1
        elements.append(Element(
            id=f"B-{index:03d}",
            type="beam",
            geometry={"start": list(segment.start), "end": list(segment.end),
                      "width": default_section[0], "depth": default_section[1]},
            params={"width": default_section[0], "depth": default_section[1]},
            evidence={"layer": segment.layer, "source": segment.id},
            confidence=_confidence(rules, layer_hit=True, geometry_ok=True, text_hit=False),
            source_entities=[segment.id],
        ))
        ambiguities.append({
            "element_id": elements[-1].id,
            "kind": "section_missing",
            "detail": {"reason": "缺少梁截面标注，使用默认截面", "layer": segment.layer},
        })

    return elements, ambiguities


def detect_components(parsed: ParsedDrawing, rules: Dict[str, Any], threshold: float,
                      element_types: List[str] | None = None) -> Tuple[List[Element], List[Dict[str, Any]], Dict[str, Any]]:
    enabled = element_types or ["grid", "column", "wall", "beam"]
    elements: List[Element] = []
    ambiguities: List[Dict[str, Any]] = []

    if "grid" in enabled:
        elements.extend(detect_grids(parsed, rules))
    if "column" in enabled:
        cols, amb = detect_columns(parsed, rules)
        elements.extend(cols)
        ambiguities.extend(amb)
    if "wall" in enabled:
        walls, amb = detect_walls(parsed, rules)
        elements.extend(walls)
        ambiguities.extend(amb)
    if "beam" in enabled:
        beams, amb = detect_beams(parsed, rules)
        elements.extend(beams)
        ambiguities.extend(amb)

    low_confidence_ids = [e.id for e in elements if e.confidence < threshold]
    known = {a["element_id"] for a in ambiguities}
    for element_id in low_confidence_ids:
        if element_id not in known:
            element = next(e for e in elements if e.id == element_id)
            ambiguities.append({
                "element_id": element_id,
                "kind": "low_confidence",
                "detail": {"confidence": element.confidence, "reason": "证据不足"},
            })

    stats = {
        "total": len(elements),
        "by_type": {t: sum(1 for e in elements if e.type == t) for t in ("grid", "column", "wall", "beam")},
        "mean_confidence": round(sum(e.confidence for e in elements) / len(elements), 2) if elements else 0.0,
        "low_confidence": len(low_confidence_ids),
        "low_confidence_ratio": round(len(low_confidence_ids) / len(elements), 2) if elements else 0.0,
    }
    return elements, ambiguities, stats

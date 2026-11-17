"""Split one drawing file into *plan regions* and assign storey identity.

Production drawings are not one file per floor.  A single DWG often carries
several plans laid out side by side, a sheet may hold a plan plus a title
block and a legend, and the same project is frequently split across many
files.  Nothing here assumes a layout template: regions are derived from the
drawing itself.

Two independent evidences are combined:

* **level labels** - text such as ``1F 一层平面``, ``二层``, ``±0.000``.  When
  two or more plan titles exist, every graphic item is assigned to the
  nearest title (Voronoi partition in page space).  This works for side by
  side plans and for plans sharing one outer frame.
* **whitespace bands** - when there is at most one title, the file is split
  along the widest empty band between graphics, but only if both halves look
  like a plan (closed outlines, block inserts or a run of long lines).
  Otherwise the file stays one region, which is the classic single-sheet case.

Every region carries its own local origin: plans drawn side by side share a
sheet coordinate system, but they must not share a Revit origin, so element
coordinates are translated by the region's lower-left corner before they
reach the detector.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from ..models import ParsedDrawing

Box = Tuple[float, float, float, float]

# ---- text vocabulary -------------------------------------------------------
_CN_DIGITS = "〇一二三四五六七八九十"

# A *plan title*: "1F 一层平面", "二层平面图", "首层", "地下二层", "F1".
_TITLE_PATTERNS = (
    re.compile(r"地下\s*(?P<n>\d{1,2}|[一二三四五六七八九十]+)\s*层"),
    re.compile(r"(?<![\.\d])(?P<n>\d{1,2})\s*[Ff](?![A-Za-z0-9])"),
    re.compile(r"(?P<n>\d{1,2})\s*层"),
    re.compile(r"(?<![A-Za-z])[Ff]\s*(?P<n>\d{1,2})(?![0-9])"),
    re.compile(r"(?P<n>[一二三四五六七八九十]+)\s*层"),
)
_FIRST_FLOOR_WORDS = ("首层", "底层", "一层", "1F", "F1")
_ROOF_WORDS = ("屋顶", "屋面", "顶层")

# An elevation note: "±0.000", "▽-5.400", "标高 3.300", "EL +12.600".
_ELEV_PATTERN = re.compile(
    r"(?:±|▽|标高|EL|E\.L\.|\\bL\\b)\s*(?P<v>[-+]?\d{1,3}\.\d{1,3})"
)
_ELEV_BARE = re.compile(r"^(?P<v>[-+]\d{1,3}\.\d{1,3})$")
_ELEV_METRE = re.compile(r"^(?P<v>[-+]?\d{1,2}\.\d{2,3})$")

DEFAULT_REGION_RULES: Dict[str, float] = {
    # a band narrower than this is drafting noise, not a sheet separation
    "min_gap_ratio": 0.06,
    "min_gap_mm": 1200.0,
    # geometry inside this radius of a region is attributed to that region
    "label_radius_ratio": 0.10,
}


@dataclass
class Region:
    """One plan area inside one drawing file."""

    id: str
    bbox: Box
    level: str = ""
    level_label: str = ""
    elevation_mm: Optional[float] = None
    evidence: Dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    segments: List[int] = field(default_factory=list)      # indices into parsed.segments
    polylines: List[int] = field(default_factory=list)
    blocks: List[int] = field(default_factory=list)
    texts: List[int] = field(default_factory=list)
    source_file: str = ""

    @property
    def size(self) -> Tuple[float, float]:
        x0, y0, x1, y1 = self.bbox
        return x1 - x0, y1 - y0

    @property
    def center(self) -> Tuple[float, float]:
        x0, y0, x1, y1 = self.bbox
        return (x0 + x1) / 2.0, (y0 + y1) / 2.0

    @property
    def offset(self) -> Tuple[float, float]:
        return self.bbox[0], self.bbox[1]

    def to_dict(self, translate: bool = False) -> Dict[str, Any]:
        x0, y0, x1, y1 = self.bbox
        return {
            "id": self.id,
            "level": self.level,
            "level_label": self.level_label,
            "elevation_mm": self.elevation_mm,
            "bbox": [round(v, 1) for v in (x0, y0, x1, y1)],
            "size": [round(v, 1) for v in self.size],
            "offset": [round(v, 1) for v in self.offset],
            "source_file": self.source_file,
            "confidence": round(self.confidence, 2),
            "evidence": dict(self.evidence),
            "counts": {
                "segments": len(self.segments),
                "polylines": len(self.polylines),
                "blocks": len(self.blocks),
                "texts": len(self.texts),
            },
            # membership is part of the contract: without these indices the
            # region cannot be sliced back out of the parsed drawing
            "segments": list(self.segments),
            "polylines": list(self.polylines),
            "blocks": list(self.blocks),
            "texts": list(self.texts),
        }


# --------------------------------------------------------------------------
# text helpers
# --------------------------------------------------------------------------
def _cn_to_int(token: str) -> Optional[int]:
    if token.isdigit():
        return int(token)
    digits = {c: i for i, c in enumerate(_CN_DIGITS)}
    if not token or any(c not in digits for c in token):
        return None
    if "十" not in token:
        return digits[token[0]] if len(token) == 1 else None
    head, _, tail = token.partition("十")
    tens = digits[head] if head else 1
    ones = digits[tail] if tail else 0
    return tens * 10 + ones


def parse_level_title(text: str) -> Optional[str]:
    """Return a normalized storey key (``1F``, ``B2``) from a plan title."""
    content = (text or "").strip()
    if not content or len(content) > 40:
        return None
    for word in _ROOF_WORDS:
        if word in content:
            return "RF"
    for word in _FIRST_FLOOR_WORDS:
        if content.startswith(word) or f" {word}" in content or content == word:
            return "1F"
    for pattern in _TITLE_PATTERNS:
        match = pattern.search(content)
        if not match:
            continue
        number = _cn_to_int(match.group("n") or "")
        if not number or number > 60:
            continue
        if "地下" in match.group(0):
            return f"B{number}"
        return f"{number}F"
    return None


def parse_elevation(text: str) -> Optional[float]:
    """Return an elevation in millimetres from a drawing note."""
    content = (text or "").strip()
    match = _ELEV_PATTERN.search(content)
    if match:
        return round(float(match.group("v")) * 1000.0, 1)
    bare = _ELEV_BARE.match(content)
    if bare:
        return round(float(bare.group("v")) * 1000.0, 1)
    # a short standalone metric value next to a plan title is an elevation
    # note; drawings write lengths in millimetres, so "3.300" can only be a
    # storey elevation and never a 3.3 mm dimension.
    metre = _ELEV_METRE.match(content)
    if metre:
        value = float(metre.group("v"))
        if -20.0 <= value <= 200.0:
            return round(value * 1000.0, 1)
    return None


def level_sort_key(level: str, elevation: Optional[float] = None) -> Tuple[int, float]:
    """Basements first, then storeys by number, unknown last."""
    if not level:
        return (2, elevation if elevation is not None else 1e9)
    if level.startswith("B") and level[1:].isdigit():
        return (-1, -int(level[1:]))
    if level.endswith("F") and level[:-1].isdigit():
        return (0, int(level[:-1]))
    if level == "RF":
        return (1, 1e6)
    return (2, elevation if elevation is not None else 0.0)


# --------------------------------------------------------------------------
# geometry helpers
# --------------------------------------------------------------------------
def _box_union(boxes: Iterable[Box]) -> Box:
    xs0, ys0, xs1, ys1 = [], [], [], []
    for x0, y0, x1, y1 in boxes:
        xs0.append(x0)
        ys0.append(y0)
        xs1.append(x1)
        ys1.append(y1)
    if not xs0:
        return (0.0, 0.0, 0.0, 0.0)
    return (min(xs0), min(ys0), max(xs1), max(ys1))


def _box_of_points(points: Sequence[Sequence[float]]) -> Box:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (min(xs), min(ys), max(xs), max(ys))


def _box_center(box: Box) -> Tuple[float, float]:
    return (box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0


def _largest_gap(boxes: Sequence[Box], axis: int) -> Tuple[float, float, float]:
    """Return (gap_width, gap_start, gap_end) on one axis."""
    spans = sorted(((b[axis], b[axis + 2]) if axis == 0 else (b[1], b[3])) for b in boxes)
    best = (0.0, 0.0, 0.0)
    reach = spans[0][1]
    for lo, hi in spans[1:]:
        if lo - reach > best[0]:
            best = (lo - reach, reach, lo)
        reach = max(reach, hi)
    return best


def _graphics_items(parsed: ParsedDrawing) -> List[Dict[str, Any]]:
    """Geometry items with their boxes; text is handled separately."""
    items: List[Dict[str, Any]] = []
    for index, segment in enumerate(parsed.segments):
        items.append({"kind": "segment", "index": index,
                      "box": _box_of_points([segment.start, segment.end]),
                      "length": segment.length})
    for index, polyline in enumerate(parsed.polylines):
        points = polyline.get("points") or []
        if len(points) < 2:
            continue
        items.append({"kind": "polyline", "index": index,
                      "box": _box_of_points(points),
                      "closed": bool(polyline.get("closed")),
                      "length": 0.0})
    for index, block in enumerate(parsed.blocks):
        x, y = block.get("insert", (0.0, 0.0))
        items.append({"kind": "block", "index": index,
                      "box": (x, y, x, y), "length": 0.0})
    return items


def _plan_like(items: Sequence[Dict[str, Any]]) -> bool:
    """A group is a plan when it owns outlines or a run of long members.

    A title block has a frame but no columns; a legend has small symbols only;
    a structural plan has closed outlines (columns/walls) or many long lines
    (axes, beams, wall faces).
    """
    if len(items) < 6:
        return False
    closed = sum(1 for item in items if item["kind"] == "polyline" and item.get("closed"))
    blocks = sum(1 for item in items if item["kind"] == "block")
    long_lines = sum(1 for item in items if item["kind"] == "segment" and item["length"] >= 1500)
    return closed >= 1 or blocks >= 2 or long_lines >= 5


def _split_by_whitespace(items: List[Dict[str, Any]], gap_min: float,
                         depth: int = 0) -> List[List[Dict[str, Any]]]:
    """Recursively cut along the widest empty band, only when both halves are plans."""
    if depth >= 6 or len(items) < 12:
        return [items]
    boxes = [item["box"] for item in items]
    gap_x = _largest_gap(boxes, 0)
    gap_y = _largest_gap(boxes, 1)
    gap = gap_x if gap_x[0] >= gap_y[0] else gap_y
    axis = 0 if gap_x[0] >= gap_y[0] else 1
    if gap[0] < gap_min:
        return [items]
    cut = (gap[1] + gap[2]) / 2.0
    left = [item for item in items if _box_center(item["box"])[axis] < cut]
    right = [item for item in items if _box_center(item["box"])[axis] >= cut]
    if not _plan_like(left) or not _plan_like(right):
        return [items]
    return (_split_by_whitespace(left, gap_min, depth + 1)
            + _split_by_whitespace(right, gap_min, depth + 1))


def _assign_by_nearest_label(items: List[Dict[str, Any]], labels: Sequence[Tuple[float, float]]
                             ) -> List[List[Dict[str, Any]]]:
    groups: List[List[Dict[str, Any]]] = [[] for _ in labels]
    for item in items:
        cx, cy = _box_center(item["box"])
        best_index, best_distance = 0, float("inf")
        for index, (lx, ly) in enumerate(labels):
            distance = ((cx - lx) ** 2 + (cy - ly) ** 2) ** 0.5
            if distance < best_distance:
                best_index, best_distance = index, distance
        groups[best_index].append(item)
    return groups


def _nearest_group(box: Box, candidates: Sequence[Box]) -> int:
    own = _box_center(box)
    best, best_distance = -1, float("inf")
    for other, other_box in enumerate(candidates):
        cx, cy = _box_center(other_box)
        distance = ((own[0] - cx) ** 2 + (own[1] - cy) ** 2) ** 0.5
        if distance < best_distance:
            best, best_distance = other, distance
    return best


# --------------------------------------------------------------------------
# main entry
# --------------------------------------------------------------------------
def detect_regions(parsed: ParsedDrawing, rules: Optional[Dict[str, Any]] = None,
                   source_file: str = "") -> List[Region]:
    """Split a parsed drawing into plan regions (one region for a plain sheet)."""
    config = {**DEFAULT_REGION_RULES, **(rules or {}).get("regions", {})}
    items = _graphics_items(parsed)
    texts = parsed.texts
    file_name = source_file or parsed.file_name

    if not items:
        return []

    overall = _box_union([item["box"] for item in items])
    span = max(overall[2] - overall[0], overall[3] - overall[1], 1000.0)
    gap_min = max(config["min_gap_mm"], span * config["min_gap_ratio"])
    label_radius = max(1500.0, span * config["label_radius_ratio"])

    # ---- 1. plan titles (only texts that name a storey) -------------------
    titles: List[Tuple[str, Tuple[float, float], str]] = []
    elevations: List[Tuple[float, Tuple[float, float], str]] = []
    for text in texts:
        level = parse_level_title(text.content)
        if level:
            titles.append((level, text.insert, text.content))
            continue
        elevation = parse_elevation(text.content)
        if elevation is not None:
            elevations.append((elevation, text.insert, text.content))

    if len(titles) >= 2:
        groups = _assign_by_nearest_label(items, [point for _, point, _ in titles])
    else:
        groups = _split_by_whitespace(items, gap_min)

    # keep only plan-like groups; attach the rest to the closest plan
    boxes = [_box_union([item["box"] for item in group]) if group else (0, 0, 0, 0)
             for group in groups]
    plan_groups = [index for index, group in enumerate(groups) if _plan_like(group)]
    if not plan_groups:
        plan_groups = [max(range(len(groups)),
                           key=lambda i: len(groups[i]))] if groups else []
    merged: List[List[Dict[str, Any]]] = [list(groups[i]) for i in plan_groups]
    merged_boxes: List[Box] = [boxes[i] for i in plan_groups]
    for index, group in enumerate(groups):
        if index in plan_groups or not group:
            continue
        target = _nearest_group(boxes[index], merged_boxes)
        if target < 0:
            continue
        merged[target].extend(group)
        merged_boxes[target] = _box_union([merged_boxes[target], boxes[index]])

    # ---- 2. per-region identity -----------------------------------------
    regions: List[Region] = []
    used_levels: List[str] = []
    for order, group in enumerate(merged, 1):
        box = _box_union([item["box"] for item in group])
        expanded = (box[0] - label_radius, box[1] - label_radius,
                    box[2] + label_radius, box[3] + label_radius)
        own_titles = [(level, point, raw) for level, point, raw in titles
                      if _inside(expanded, point)]
        # a title already claimed by a smaller neighbour must not be reused
        level, level_label, evidence = "", "", {}
        for candidate in own_titles:
            if candidate[0] in used_levels:
                continue
            level, level_label = candidate[0], candidate[2]
            evidence = {"source": "title", "text": candidate[2], "x": candidate[1][0],
                        "y": candidate[1][1]}
            used_levels.append(level)
            break
        elevation, elevation_text = None, ""
        for value, point, raw in elevations:
            if _inside(expanded, point) and (elevation is None or abs(value) < abs(elevation)):
                elevation, elevation_text = value, raw

        region = Region(
            id=f"{Path(file_name).stem}-R{order}" if file_name else f"R{order}",
            bbox=box, level=level, level_label=level_label,
            elevation_mm=elevation, evidence=evidence,
            source_file=file_name,
        )
        if elevation_text:
            region.evidence.setdefault("elevation_text", elevation_text)
        if not level and elevation is not None:
            region.level = f"EL{elevation / 1000.0:.3f}"
            region.evidence.setdefault("level_from", "elevation")
        region.confidence = _region_confidence(region)
        for item in group:
            if item["kind"] == "segment":
                region.segments.append(item["index"])
            elif item["kind"] == "polyline":
                region.polylines.append(item["index"])
            elif item["kind"] == "block":
                region.blocks.append(item["index"])
        for index, text in enumerate(texts):
            if index in region.texts:
                continue
            if _inside(expanded, text.insert):
                region.texts.append(index)
        regions.append(region)

    # labels that name the same storey twice (two files, or a duplicated plan)
    for region in regions:
        if region.level and sum(1 for other in regions if other.level == region.level) > 1:
            region.evidence.setdefault("duplicate_level", True)
            region.confidence = round(region.confidence * 0.8, 2)

    regions.sort(key=lambda r: (level_sort_key(r.level, r.elevation_mm),
                                r.bbox[1], r.bbox[0]))
    for order, region in enumerate(regions, 1):
        if len(regions) > 1:
            region.id = f"R{order}"
    return regions


def _inside(box: Box, point: Sequence[float]) -> bool:
    return box[0] <= point[0] <= box[2] and box[1] <= point[1] <= box[3]


def _region_confidence(region: Region) -> float:
    score = 0.3
    if region.level:
        score += 0.45
    if region.elevation_mm is not None:
        score += 0.1
    if (region.evidence or {}).get("source") == "title":
        score += 0.1
    counts = len(region.segments) + len(region.polylines) + len(region.blocks)
    if counts >= 20:
        score += 0.05
    return round(min(1.0, score), 2)


def build_region_drawing(parsed: ParsedDrawing, region: Region,
                         translate: bool = True) -> ParsedDrawing:
    """Slice the parsed drawing down to one region, optionally re-based to (0,0)."""
    dx, dy = region.offset if translate else (0.0, 0.0)

    def shift_point(point):
        return (round(point[0] - dx, 3), round(point[1] - dy, 3))

    segments = []
    for index in region.segments:
        segment = parsed.segments[index]
        segments.append(type(segment)(
            segment.id, shift_point(segment.start), shift_point(segment.end),
            segment.layer, segment.source))

    polylines = []
    for index in region.polylines:
        polyline = dict(parsed.polylines[index])
        polyline["points"] = [shift_point(p) for p in polyline.get("points", [])]
        polylines.append(polyline)

    blocks = []
    for index in region.blocks:
        block = dict(parsed.blocks[index])
        block["insert"] = shift_point(block.get("insert", (0.0, 0.0)))
        blocks.append(block)

    texts = []
    for index in region.texts:
        text = parsed.texts[index]
        texts.append(type(text)(text.id, text.content, shift_point(text.insert),
                                text.layer, text.height))

    return ParsedDrawing(
        task_id=parsed.task_id,
        file_name=parsed.file_name,
        unit_scale=parsed.unit_scale,
        layers=parsed.layers,
        segments=segments,
        polylines=polylines,
        blocks=blocks,
        texts=texts,
        sheets=[{"id": region.id, "level": region.level,
                 "scale": (parsed.sheets[0].get("scale", "unknown") if parsed.sheets else "unknown"),
                 "discipline": (parsed.sheets[0].get("discipline", "structure")
                                if parsed.sheets else "structure")}],
        health=parsed.health,
    )


def region_texts(parsed: ParsedDrawing, region: Region) -> List[Dict[str, Any]]:
    """Texts belonging to a region, for level/elevation lookups by callers."""
    return [parsed.texts[index].to_dict() for index in region.texts]


__all__ = [
    "Region",
    "DEFAULT_REGION_RULES",
    "detect_regions",
    "build_region_drawing",
    "parse_level_title",
    "parse_elevation",
    "level_sort_key",
]

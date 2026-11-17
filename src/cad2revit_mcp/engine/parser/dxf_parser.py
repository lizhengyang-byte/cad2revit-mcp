"""DXF parsing and normalization.

The parser only produces geometry data.  It never decides what a line *means*;
that is the detector's job.  Everything is converted to millimetres so the
rest of the pipeline can ignore drawing units.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

import ezdxf

from ...errors import E_INPUT_FILE, E_INPUT_HEALTH, AppError
from ...geometry import dedupe_segments, is_closed_polyline
from ...models import ParsedDrawing, Segment, TextItem

UNIT_SCALE = {
    0: 1.0,      # unitless, assume mm
    1: 25.4,     # inch
    2: 304.8,    # foot
    4: 1.0,      # mm
    5: 10.0,     # cm
    6: 1000.0,   # m
    8: 0.0254,   # microinch (rare)
    9: 2.54,     # mil
    10: 914.4,   # yard
}

SCALE_PATTERN = re.compile(r"1\s*[:：]\s*(\d{2,4})")
LEVEL_PATTERN = re.compile(r"(?P<level>[0-9一二三四五六七八九十]+)\s*层|F(?P<f>[0-9]+)")


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _maybe_convert_dwg(path: Path, converter: str) -> Path:
    if path.suffix.lower() != ".dwg":
        return path
    if not converter:
        raise AppError(
            E_INPUT_FILE,
            "DWG input requires a configured converter; save as DXF or set dwg_converter",
            {"file": path.name},
        )
    target = path.with_suffix(".dxf")
    try:
        subprocess.run([converter, str(path), str(target)], check=True, timeout=300)
    except Exception as exc:  # pragma: no cover - depends on external tool
        raise AppError(E_INPUT_FILE, f"DWG conversion failed: {exc}") from exc
    return target


def _polyline_points(entity, scale: float) -> List[Tuple[float, float]]:
    if entity.dxftype() == "LWPOLYLINE":
        return [(p[0] * scale, p[1] * scale) for p in entity.get_points("xy")]
    if entity.dxftype() == "POLYLINE":
        return [(v.dxf.location.x * scale, v.dxf.location.y * scale)
                for v in entity.vertices]
    return []


def parse_dxf(path: Path, config) -> ParsedDrawing:
    path = _maybe_convert_dwg(path, config.dwg_converter)
    if not path.exists():
        raise AppError(E_INPUT_FILE, f"file not found: {path.name}")

    try:
        doc = ezdxf.readfile(str(path))
    except Exception as exc:
        raise AppError(E_INPUT_FILE, f"cannot read drawing: {exc}") from exc

    scale = UNIT_SCALE.get(int(doc.header.get("$INSUNITS", 4) or 4), 1.0)
    msp = doc.modelspace()

    segments: List[Segment] = []
    polylines: List[Dict[str, Any]] = []
    blocks: List[Dict[str, Any]] = []
    texts: List[TextItem] = []
    layer_stats: Dict[str, Dict[str, int]] = {}

    def touch(layer: str, kind: str) -> None:
        entry = layer_stats.setdefault(layer or "0", {"LINE": 0, "LWPOLYLINE": 0,
                                                       "POLYLINE": 0, "INSERT": 0,
                                                       "TEXT": 0})
        entry[kind] = entry.get(kind, 0) + 1

    seq_seg = seq_pl = seq_blk = seq_txt = 0
    for entity in msp:
        kind = entity.dxftype()
        layer = getattr(entity.dxf, "layer", "0")
        if kind == "LINE":
            seq_seg += 1
            start = (entity.dxf.start.x * scale, entity.dxf.start.y * scale)
            end = (entity.dxf.end.x * scale, entity.dxf.end.y * scale)
            segments.append(Segment(f"L{seq_seg:05d}", start, end, layer, "LINE"))
            touch(layer, "LINE")
        elif kind in ("LWPOLYLINE", "POLYLINE"):
            points = _polyline_points(entity, scale)
            if not points:
                continue
            seq_pl += 1
            closed = is_closed_polyline(points) or bool(getattr(entity, "closed", False))
            polylines.append({
                "id": f"P{seq_pl:05d}",
                "layer": layer,
                "closed": closed,
                "points": points,
                "source": kind,
            })
            touch(layer, kind)
            if not closed and len(points) >= 2:
                for i in range(len(points) - 1):
                    seq_seg += 1
                    segments.append(Segment(f"L{seq_seg:05d}", points[i], points[i + 1],
                                            layer, "POLYLINE"))
        elif kind == "INSERT":
            seq_blk += 1
            name = getattr(entity.dxf, "name", "") or ""
            insert = (entity.dxf.insert.x * scale, entity.dxf.insert.y * scale)
            rotation = float(getattr(entity.dxf, "rotation", 0.0) or 0.0)
            blocks.append({"id": f"B{seq_blk:05d}", "name": name, "layer": layer,
                           "insert": insert, "rotation": rotation})
            touch(layer, "INSERT")
        elif kind in ("TEXT", "MTEXT"):
            seq_txt += 1
            content = entity.plain_text() if kind == "MTEXT" else entity.dxf.text
            insert = entity.dxf.insert
            texts.append(TextItem(
                id=f"T{seq_txt:05d}",
                content=(content or "").strip(),
                insert=(insert.x * scale, insert.y * scale),
                layer=layer,
                height=float(getattr(entity.dxf, "height", 0.0) or 0.0) * scale,
            ))
            touch(layer, "TEXT")

    segments = dedupe_segments(segments)

    total_entities = len(segments) + len(polylines) + len(blocks) + len(texts)
    line_ratio = len(segments) / max(total_entities, 1)

    scale_value = None
    for text in texts:
        match = SCALE_PATTERN.search(text.content)
        if match:
            scale_value = f"1:{match.group(1)}"
            break

    level_name = ""
    for text in texts:
        match = LEVEL_PATTERN.search(text.content)
        if match:
            token = match.group("level") or match.group("f") or ""
            level_name = f"{token}F" if token.isdigit() else text.content
            break

    sheets = [{
        "id": path.stem,
        "level": level_name,
        "scale": scale_value or "unknown",
        "discipline": "structure",
    }]

    problems: List[Dict[str, str]] = []
    if int(doc.header.get("$INSUNITS", 0) or 0) == 0:
        problems.append({"code": "unit_unknown", "message": "INSUNITS未设置，按毫米处理"})
    if not scale_value:
        problems.append({"code": "scale_missing", "message": "未找到比例标注，按图纸实际尺寸处理"})
    if total_entities < 20:
        problems.append({"code": "too_few_entities", "message": "图元数量过少，可能不是一张完整平面图"})
    if line_ratio > 0.85 and not blocks:
        problems.append({"code": "exploded", "message": "图形以散线为主且无图块，墙柱轮廓可能已被炸开"})

    hard_fail = any(p["code"] == "too_few_entities" for p in problems)
    health = {
        "usable": not hard_fail,
        "problems": problems,
        "insunits": int(doc.header.get("$INSUNITS", 0) or 0),
    }
    if hard_fail:
        raise AppError(E_INPUT_HEALTH, "drawing failed health check",
                       {"problems": problems})

    layers = [{"name": name, **counts} for name, counts in sorted(layer_stats.items())]

    return ParsedDrawing(
        task_id="",
        file_name=path.name,
        unit_scale=scale,
        layers=layers,
        segments=segments,
        polylines=polylines,
        blocks=blocks,
        texts=texts,
        sheets=sheets,
        health=health,
    )


def parsed_summary(parsed: ParsedDrawing, duplicates: int = 0) -> Dict[str, Any]:
    return {
        "file_name": parsed.file_name,
        "unit_scale": parsed.unit_scale,
        "segments": len(parsed.segments),
        "polylines": len(parsed.polylines),
        "blocks": len(parsed.blocks),
        "texts": len(parsed.texts),
        "duplicate_segments": duplicates,
    }


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")

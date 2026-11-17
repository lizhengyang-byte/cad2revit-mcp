"""Core data model of the CAD to Revit pipeline.

The IR (intermediate representation) is the only contract between the
recognition layer and the Revit execution layer.  See docs/04 of the
project documentation set for the field definition.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

IR_SCHEMA_VERSION = "1.0"

ELEMENT_TYPES = ("grid", "column", "wall", "beam")


@dataclass
class Element:
    id: str
    type: str
    geometry: Dict[str, Any]
    params: Dict[str, Any] = field(default_factory=dict)
    evidence: Dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    source_entities: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "geometry": dict(self.geometry),
            "params": dict(self.params),
            "evidence": dict(self.evidence),
            "confidence": round(float(self.confidence), 2),
            "source_entities": list(self.source_entities),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Element":
        return cls(
            id=str(data["id"]),
            type=str(data["type"]),
            geometry=dict(data.get("geometry") or {}),
            params=dict(data.get("params") or {}),
            evidence=dict(data.get("evidence") or {}),
            confidence=float(data.get("confidence", 0.0)),
            source_entities=list(data.get("source_entities") or []),
        )


@dataclass
class IRDocument:
    task_id: str
    sheet: Dict[str, Any]
    elements: List[Element] = field(default_factory=list)
    ambiguous: List[str] = field(default_factory=list)
    schema_version: str = IR_SCHEMA_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "task_id": self.task_id,
            "sheet": dict(self.sheet),
            "elements": [e.to_dict() for e in self.elements],
            "ambiguous": list(self.ambiguous),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "IRDocument":
        return cls(
            task_id=str(data["task_id"]),
            sheet=dict(data.get("sheet") or {}),
            elements=[Element.from_dict(e) for e in data.get("elements", [])],
            ambiguous=list(data.get("ambiguous") or []),
            schema_version=str(data.get("schema_version", IR_SCHEMA_VERSION)),
        )


@dataclass
class Segment:
    """A normalized line segment in millimetres."""

    id: str
    start: tuple[float, float]
    end: tuple[float, float]
    layer: str
    source: str = "LINE"

    @property
    def length(self) -> float:
        dx = self.end[0] - self.start[0]
        dy = self.end[1] - self.start[1]
        return (dx * dx + dy * dy) ** 0.5

    def direction(self) -> tuple[float, float]:
        if self.length == 0:
            return (1.0, 0.0)
        return ((self.end[0] - self.start[0]) / self.length,
                (self.end[1] - self.start[1]) / self.length)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "start": list(self.start),
            "end": list(self.end),
            "layer": self.layer,
            "source": self.source,
        }


@dataclass
class TextItem:
    id: str
    content: str
    insert: tuple[float, float]
    layer: str = ""
    height: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "content": self.content,
            "insert": list(self.insert),
            "layer": self.layer,
            "height": self.height,
        }


@dataclass
class ParsedDrawing:
    """Normalized drawing produced by the parser module."""

    task_id: str
    file_name: str
    unit_scale: float
    layers: List[Dict[str, Any]] = field(default_factory=list)
    segments: List[Segment] = field(default_factory=list)
    polylines: List[Dict[str, Any]] = field(default_factory=list)
    blocks: List[Dict[str, Any]] = field(default_factory=list)
    texts: List[TextItem] = field(default_factory=list)
    sheets: List[Dict[str, Any]] = field(default_factory=list)
    health: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "file_name": self.file_name,
            "unit_scale": self.unit_scale,
            "layers": self.layers,
            "sheets": self.sheets,
            "texts": [t.to_dict() for t in self.texts],
            "health": self.health,
            "stats": {
                "segments": len(self.segments),
                "polylines": len(self.polylines),
                "blocks": len(self.blocks),
                "texts": len(self.texts),
            },
        }


def element_sort_key(element: Element) -> str:
    return element.id


def required_params(element_type: str) -> List[str]:
    return {
        "column": ["material", "level"],
        "wall": ["thickness", "level"],
        "beam": ["width", "depth", "level"],
        "grid": ["name"],
    }.get(element_type, [])


def element_title(element_type: str) -> str:
    return {
        "grid": "轴网",
        "column": "柱",
        "wall": "墙",
        "beam": "梁",
    }.get(element_type, element_type)


__all__ = [
    "Element",
    "IRDocument",
    "IR_SCHEMA_VERSION",
    "ELEMENT_TYPES",
    "Segment",
    "TextItem",
    "ParsedDrawing",
    "required_params",
    "element_title",
]

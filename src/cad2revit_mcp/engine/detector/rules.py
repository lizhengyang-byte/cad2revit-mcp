"""Rule library loading and layer matching."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

_PACKAGE_RULES = Path(__file__).resolve().parents[2] / "rules" / "structure_default.json"
_REPO_RULES = Path(__file__).resolve().parents[4] / "rules" / "structure_default.json"

# Installed package first, source checkout second: ``pip install`` users have no
# repository root, editable users may have edited the repo copy.
DEFAULT_RULES_PATH = _PACKAGE_RULES if _PACKAGE_RULES.exists() else _REPO_RULES


def load_rules(path: Optional[str | Path] = None) -> Dict[str, Any]:
    candidate = Path(path) if path else DEFAULT_RULES_PATH
    if not candidate.exists():
        raise FileNotFoundError(f"rule set not found: {candidate}")
    return json.loads(candidate.read_text(encoding="utf-8"))


@lru_cache(maxsize=512)
def _compiled(pattern: str) -> re.Pattern:
    return re.compile(pattern)


def match_layer(layer: str, patterns: Sequence[str]) -> bool:
    if not layer:
        return False
    return any(_compiled(p).search(layer) for p in patterns)


def first_type_match(layer: str, rules: Dict[str, Any]) -> Optional[str]:
    for element_type in ("column", "wall", "beam", "grid"):
        if match_layer(layer, rules["layers"].get(element_type, [])):
            return element_type
    return None


def parse_section(text: str, pattern: str) -> Optional[Tuple[float, float]]:
    match = re.search(pattern, text)
    if not match:
        return None
    groups = [g for g in match.groups() if g and g.isdigit()]
    if len(groups) < 2:
        return None
    return float(groups[0]), float(groups[1])


def section_label_in(text: str) -> Optional[Tuple[float, float]]:
    match = re.search(r"(\d{3,4})\s*[xX×*]\s*(\d{3,4})", text)
    if not match:
        return None
    return float(match.group(1)), float(match.group(2))


def nearest_text(texts: Iterable, point: Tuple[float, float], max_distance: float):
    best = None
    best_distance = max_distance
    for text in texts:
        dx = text.insert[0] - point[0]
        dy = text.insert[1] - point[1]
        dist = (dx * dx + dy * dy) ** 0.5
        if dist <= best_distance:
            best, best_distance = text, dist
    return best


def build_layer_index(layers: List[Dict[str, Any]]) -> Dict[str, int]:
    return {layer["name"]: sum(v for k, v in layer.items() if k != "name") for layer in layers}

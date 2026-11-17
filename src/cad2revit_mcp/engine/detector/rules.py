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

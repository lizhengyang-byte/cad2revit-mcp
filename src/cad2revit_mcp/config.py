"""Configuration handling.

Every path that the tool touches must live inside ``allowed_paths``; the
check is done in :meth:`Config.resolve_path` so no module needs to repeat it.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

from .errors import E_INPUT_PATH, AppError

DEFAULT_FAMILY_MAP: Dict[str, Dict[str, str]] = {
    "column": {"family": "结构柱-混凝土矩形", "type": "矩形柱"},
    "wall": {"family": "基本墙", "type": "常规 - 200mm"},
    "beam": {"family": "混凝土矩形梁", "type": "矩形梁"},
    "grid": {"family": "轴网", "type": "轴网"},
}

DEFAULT_LEVEL_MAP: Dict[str, float] = {"1F": 0.0, "2F": 3000.0, "3F": 6000.0}


@dataclass
class Config:
    workspace_dir: Path
    allowed_paths: List[Path] = field(default_factory=list)
    confidence_threshold: float = 0.75
    review_threshold: float = 0.90
    batch_size: int = 80
    rule_set_version: str = "structure-2026.11"
    default_level: str = "1F"
    bridge_url: str = ""
    dwg_converter: str = ""
    log_level: str = "info"
    family_map: Dict[str, Dict[str, str]] = field(default_factory=lambda: dict(DEFAULT_FAMILY_MAP))
    level_map: Dict[str, float] = field(default_factory=lambda: dict(DEFAULT_LEVEL_MAP))

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Config":
        workspace = Path(os.environ.get("CAD2REVIT_WORKSPACE", "./workspace")).resolve()
        cfg = cls(workspace_dir=workspace)
        candidate = path or os.environ.get("CAD2REVIT_CONFIG")
        if candidate and Path(candidate).exists():
            raw = json.loads(Path(candidate).read_text(encoding="utf-8"))
            cfg = cls.from_dict(raw)
        if not cfg.allowed_paths:
            cfg.allowed_paths = [cfg.workspace_dir]
        if os.environ.get("CAD2REVIT_LOG_LEVEL"):
            cfg.log_level = os.environ["CAD2REVIT_LOG_LEVEL"]
        cfg.workspace_dir.mkdir(parents=True, exist_ok=True)
        return cfg

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "Config":
        workspace = Path(raw.get("workspace_dir", "./workspace")).resolve()
        allowed = [Path(p).resolve() for p in raw.get("allowed_paths", [])]
        return cls(
            workspace_dir=workspace,
            allowed_paths=allowed,
            confidence_threshold=float(raw.get("confidence_threshold", 0.75)),
            review_threshold=float(raw.get("review_threshold", 0.90)),
            batch_size=int(raw.get("batch_size", 80)),
            rule_set_version=str(raw.get("rule_set_version", "structure-2026.11")),
            default_level=str(raw.get("default_level", "1F")),
            bridge_url=str(raw.get("bridge_url", "")),
            dwg_converter=str(raw.get("dwg_converter", "")),
            log_level=str(raw.get("log_level", "info")),
            family_map={**DEFAULT_FAMILY_MAP, **raw.get("family_map", {})},
            level_map={**DEFAULT_LEVEL_MAP, **raw.get("level_map", {})},
        )

    def resolve_path(self, raw: str) -> Path:
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = (self.workspace_dir / path).resolve()
        else:
            path = path.resolve()
        if not any(str(path).startswith(str(root)) for root in self.allowed_paths):
            raise AppError(
                E_INPUT_PATH,
                f"path outside allowed directories: {raw}",
                {"allowed": [str(p) for p in self.allowed_paths]},
            )
        return path

    def task_dir(self, task_id: str) -> Path:
        path = self.workspace_dir / "tasks" / task_id
        path.mkdir(parents=True, exist_ok=True)
        return path

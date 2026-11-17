"""Error model shared by every tool.

Codes follow the ranges documented in the project API specification:
E-100x input problems, E-200x recognition uncertainty, E-300x mapping
problems, E-400x Revit execution failures, W-100x warnings.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

E_INPUT_PATH = "E-1001"
E_INPUT_FILE = "E-1002"
E_INPUT_HEALTH = "E-1003"
E_UNCERTAIN = "E-2001"
E_CONFLICT = "E-2002"
E_LAYER_UNKNOWN = "E-2003"
E_MAPPING_MISSING = "E-3001"
E_LEVEL_UNKNOWN = "E-3002"
E_EXEC_LOCKED = "E-4001"
E_EXEC_TRANSACTION = "E-4002"
E_EXEC_API = "E-4003"

W_PARTIAL = "W-1001"
W_PARAM_MISSING = "W-1002"
W_REPORT_PARTIAL = "W-1003"
W_LEVEL_FALLBACK = "W-1004"
W_TOPOLOGY = "W-1005"


@dataclass
class AppError(Exception):
    code: str
    message: str
    detail: Optional[Dict[str, Any]] = None

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.code}: {self.message}"

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"code": self.code, "message": self.message}
        if self.detail:
            payload["detail"] = self.detail
        return payload


def response(status: str, data: Any = None, warnings=None, error=None,
             trace_id: str = "") -> Dict[str, Any]:
    return {
        "status": status,
        "data": data if data is not None else {},
        "warnings": warnings or [],
        "error": error,
        "trace_id": trace_id,
    }


def warning(code: str, message: str) -> Dict[str, str]:
    return {"code": code, "message": message}

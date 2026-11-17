"""Execute the instruction set.

Two modes are supported:

* ``bridge``  - send batches to the local Revit add-in over HTTP.  The add-in
  runs each batch in one transaction and reports per element results.
* ``local``   - no Revit available; the plan is validated and recorded so the
  recognition pipeline can be tested end to end without Revit.

Both modes use the same idempotency keys, so re-running a task never creates
duplicate elements in either mode.
"""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from ..errors import AppError, E_EXEC_API, E_EXEC_LOCKED, W_PARTIAL, warning


def idempotency_key(element: Dict[str, Any], rule_version: str) -> str:
    payload = json.dumps({
        "element_id": element["element_id"],
        "geometry": element.get("geometry", {}),
        "rule_version": rule_version,
    }, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


class Executor:
    def __init__(self, config, store) -> None:
        self.config = config
        self.store = store

    def execute(self, task_id: str, plan: Dict[str, Any], dry_run: bool = False,
                resume: bool = True, snapshot: bool = True) -> Dict[str, Any]:
        task_dir = self.config.task_dir(task_id)
        run_dir = task_dir / "run"
        run_dir.mkdir(parents=True, exist_ok=True)

        snapshot_info = {}
        if snapshot and not dry_run:
            snapshot_info = {
                "id": f"snap-{task_id}-{datetime.now().strftime('%H%M%S')}",
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "project": self.config.bridge_url or "local-simulation",
            }
            (run_dir / "snapshot.json").write_text(
                json.dumps(snapshot_info, ensure_ascii=False, indent=2), encoding="utf-8")

        created: List[Dict[str, Any]] = []
        failed: List[Dict[str, Any]] = []
        skipped: List[Dict[str, Any]] = []
        batch_reports: List[Dict[str, Any]] = []
        key_pairs: List[tuple[str, str]] = []
        mode = "dry_run" if dry_run else ("bridge" if self.config.bridge_url else "local")

        for batch in plan.get("batches", []):
            batch_no = batch["batch_no"]
            chunk = batch["instructions"]
            if dry_run:
                batch_reports.append({"batch_no": batch_no, "success": len(chunk), "failed": 0,
                                      "instruction_hash": batch["instruction_hash"],
                                      "mode": "dry_run"})
                continue

            pending = []
            for instruction in chunk:
                key = idempotency_key(instruction, self.config.rule_set_version)
                if resume and self.store.has_key(task_id, key):
                    skipped.append({"element_id": instruction["element_id"],
                                    "reason": "already created (idempotent skip)"})
                    continue
                pending.append((key, instruction))

            if not pending:
                batch_reports.append({"batch_no": batch_no, "success": 0, "failed": 0,
                                      "instruction_hash": batch["instruction_hash"],
                                      "mode": mode})
                continue

            if mode == "bridge":
                results = self._send_bridge(pending)
            else:
                results = [{"element_id": ins["element_id"], "ok": True,
                            "revit_id": f"local-{ins['element_id']}"}
                           for _, ins in pending]

            batch_success = 0
            for (key, instruction), result in zip(pending, results):
                if result.get("ok"):
                    created.append({
                        "element_id": instruction["element_id"],
                        "revit_id": result.get("revit_id", ""),
                        "op": instruction["op"],
                        "level": instruction.get("level"),
                    })
                    key_pairs.append((key, instruction["element_id"]))
                    batch_success += 1
                else:
                    failed.append({
                        "element_id": instruction["element_id"],
                        "code": result.get("code", E_EXEC_API),
                        "message": result.get("message", "unknown execution error"),
                    })

            batch_reports.append({
                "batch_no": batch_no,
                "success": batch_success,
                "failed": len(pending) - batch_success,
                "instruction_hash": batch["instruction_hash"],
                "mode": mode,
            })
            self.store.record_run(task_id, batch_no, batch["instruction_hash"],
                                  batch_success, len(pending) - batch_success,
                                  {"results": results},
                                  datetime.now().isoformat(timespec="seconds"))

        if key_pairs:
            self.store.add_keys(task_id, key_pairs, datetime.now().isoformat(timespec="seconds"))

        (run_dir / "created.json").write_text(
            json.dumps({"created": created, "failed": failed, "skipped": skipped},
                       ensure_ascii=False, indent=2), encoding="utf-8")

        warnings = []
        if failed:
            warnings.append(warning(W_PARTIAL, f"{len(failed)} 个构件建模失败，见报告明细"))

        status = "ok" if not failed else ("partial" if created else "error")
        return {
            "status": status,
            "mode": mode,
            "created": created,
            "failed": failed,
            "skipped": skipped,
            "batches": batch_reports,
            "snapshot": snapshot_info,
            "warnings": warnings,
            "stats": {
                "created": len(created),
                "failed": len(failed),
                "skipped": len(skipped),
                "batches": len(batch_reports),
            },
        }

    def _send_bridge(self, pending) -> List[Dict[str, Any]]:
        payload = json.dumps({
            "instructions": [ins for _, ins in pending],
        }, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            f"{self.config.bridge_url.rstrip('/')}/execute",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.URLError as exc:
            raise AppError(E_EXEC_LOCKED, f"Revit bridge unreachable: {exc}") from exc
        except Exception as exc:  # pragma: no cover - defensive
            raise AppError(E_EXEC_API, f"bridge call failed: {exc}") from exc
        return list(body.get("results", []))

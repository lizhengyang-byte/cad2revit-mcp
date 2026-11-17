"""Pipeline orchestration used by both the MCP server and the CLI."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import Config
from .engine.detector.detect import detect_components
from .engine.detector.rules import load_rules
from .engine.executor import Executor
from .engine.graph import build_graph, subgraph
from .engine.parser.dxf_parser import file_hash, now_iso, parse_dxf
from .engine.planner import plan as build_plan
from .engine.reporter import build_report
from .engine.resolver import resolve as apply_decisions
from .engine.verifier import verify as verify_model
from .errors import (
    E_INPUT_FILE,
    E_MAPPING_MISSING,
    W_PARAM_MISSING,
    AppError,
    warning,
)
from .models import IRDocument, ParsedDrawing, Segment, TextItem
from .store import TaskStore


def _parsed_to_json(parsed: ParsedDrawing) -> Dict[str, Any]:
    return {
        "task_id": parsed.task_id,
        "file_name": parsed.file_name,
        "unit_scale": parsed.unit_scale,
        "layers": parsed.layers,
        "sheets": parsed.sheets,
        "health": parsed.health,
        "texts": [t.to_dict() for t in parsed.texts],
        "segments": [s.to_dict() for s in parsed.segments],
        "polylines": parsed.polylines,
        "blocks": parsed.blocks,
    }


def _parsed_from_json(data: Dict[str, Any]) -> ParsedDrawing:
    return ParsedDrawing(
        task_id=data["task_id"],
        file_name=data["file_name"],
        unit_scale=float(data["unit_scale"]),
        layers=data.get("layers", []),
        segments=[
            Segment(s["id"], tuple(s["start"]), tuple(s["end"]), s["layer"], s.get("source", "LINE"))
            for s in data.get("segments", [])
        ],
        polylines=data.get("polylines", []),
        blocks=data.get("blocks", []),
        texts=[
            TextItem(t["id"], t["content"], tuple(t["insert"]), t.get("layer", ""), float(t.get("height", 0.0)))
            for t in data.get("texts", [])
        ],
        sheets=data.get("sheets", []),
        health=data.get("health", {}),
    )


class Pipeline:
    def __init__(self, config: Optional[Config] = None) -> None:
        self.config = config or Config.load()
        self.store = TaskStore(self.config.workspace_dir / "tasks.db")
        self.executor = Executor(self.config, self.store)
        self.rules = load_rules()

    # -- helpers ---------------------------------------------------------
    def _new_task_id(self, digest: str) -> str:
        return f"t-{datetime.now().strftime('%Y%m%d')}-{digest[:4]}"

    def _task_dir(self, task_id: str) -> Path:
        path = self.config.task_dir(task_id)
        for sub in ("input", "parsed", "ir", "script", "run", "check"):
            (path / sub).mkdir(parents=True, exist_ok=True)
        return path

    def _load_parsed(self, task_id: str) -> ParsedDrawing:
        path = self._task_dir(task_id) / "parsed" / "parsed.json"
        if not path.exists():
            raise AppError(E_INPUT_FILE, f"task not parsed: {task_id}")
        return _parsed_from_json(json.loads(path.read_text(encoding="utf-8")))

    def _load_ir(self, task_id: str) -> IRDocument:
        path = self._task_dir(task_id) / "ir" / "ir.json"
        if not path.exists():
            raise AppError(E_INPUT_FILE, f"task has no IR: {task_id}")
        return IRDocument.from_dict(json.loads(path.read_text(encoding="utf-8")))

    # -- stage 1: parse ---------------------------------------------------
    def parse(self, file_path: str, discipline: str = "structure",
              level_name: str = "", convert_dwg: bool = True) -> Dict[str, Any]:
        path = self.config.resolve_path(file_path)
        if not path.exists():
            raise AppError(E_INPUT_FILE, f"file not found: {path.name}")
        digest = file_hash(path)
        task_id = self._new_task_id(digest)

        parsed = parse_dxf(path, self.config if convert_dwg else _NoConverter(self.config))
        parsed.task_id = task_id
        if level_name:
            parsed.sheets[0]["level"] = level_name
        parsed.sheets[0]["discipline"] = discipline

        task_dir = self._task_dir(task_id)
        (task_dir / "parsed" / "parsed.json").write_text(
            json.dumps(_parsed_to_json(parsed), ensure_ascii=False, indent=2), encoding="utf-8")

        self.store.create_task(task_id, parsed.file_name, digest,
                               f"trace-{task_id}", self.config.rule_set_version, now_iso())
        self.store.register_rule_version(self.config.rule_set_version,
                                         str(self.config.rule_set_version), now_iso())

        return {
            "task_id": task_id,
            "sheets": parsed.sheets,
            "layers": parsed.layers,
            "texts": [t.to_dict() for t in parsed.texts],
            "health": parsed.health,
            "stats": {
                "segments": len(parsed.segments),
                "polylines": len(parsed.polylines),
                "blocks": len(parsed.blocks),
                "texts": len(parsed.texts),
            },
        }

    # -- stage 2: detect --------------------------------------------------
    def detect(self, task_id: str, element_types: List[str] | None = None,
               rule_set: str | None = None, confidence_threshold: float | None = None) -> Dict[str, Any]:
        parsed = self._load_parsed(task_id)
        threshold = (confidence_threshold if confidence_threshold is not None
                     else self.config.confidence_threshold)
        rules = self.rules
        if rule_set and rule_set != self.config.rule_set_version:
            rules = load_rules(rule_set if rule_set.endswith(".json") else None)

        elements, ambiguities, stats = detect_components(parsed, rules, threshold, element_types)
        graph = build_graph(
            [element.to_dict() for element in elements],
            (rules or {}).get("graph", {}),
        )
        # Graph issues are first-class ambiguities: the model must resolve them
        # through resolve_ambiguities instead of silently modelling around them.
        ambiguities = [*ambiguities, *[
            {"element_id": item["element_id"], "kind": item["kind"], "detail": item["detail"]}
            for item in graph.get("ambiguities", [])
        ]]
        ir = IRDocument(task_id=task_id, sheet=parsed.sheets[0] if parsed.sheets else {},
                        elements=elements, ambiguous=[a["element_id"] for a in ambiguities],
                        graph=graph)

        task_dir = self._task_dir(task_id)
        (task_dir / "ir" / "ir.json").write_text(
            json.dumps(ir.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

        seen: set[tuple[str, str]] = set()
        for item in ambiguities:
            key = (item["element_id"], item["kind"])
            if key in seen:
                continue
            seen.add(key)
            self.store.put_ambiguity(task_id, item["element_id"], item["kind"], item["detail"])

        self.store.update_task(task_id, now_iso(), status="detected",
                               ir_path=str(task_dir / "ir" / "ir.json"))

        warnings = []
        if stats["low_confidence_ratio"] > 0.4:
            warnings.append(warning(W_PARAM_MISSING,
                                    f"低置信度占比 {stats['low_confidence_ratio']:.0%}，建议先人工确认再建模"))
        return {
            "task_id": task_id,
            "elements": [e.to_dict() for e in elements],
            "ambiguous": ambiguities,
            "graph": graph,
            "graph_summary": graph.get("summary", {}),
            "stats": stats,
            "warnings": warnings,
        }

    # -- stage 3: resolve -------------------------------------------------
    def resolve(self, task_id: str, decisions: List[Dict[str, Any]],
                apply_to_similar: bool = False, decided_by: str = "llm") -> Dict[str, Any]:
        ir = self._load_ir(task_id)
        ir, conflicts, summary = apply_decisions(ir, decisions, apply_to_similar, decided_by)

        for decision in decisions:
            if decision.get("element_id") and decision.get("action") in ("accept", "modify", "exclude"):
                self.store.resolve(task_id, decision["element_id"],
                                   decision["action"], decided_by, now_iso())

        task_dir = self._task_dir(task_id)
        (task_dir / "ir" / "ir.json").write_text(
            json.dumps(ir.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

        warnings = []
        if conflicts:
            warnings.append(warning(E_MAPPING_MISSING, f"{len(conflicts)} 条裁决无法应用"))
        return {
            "task_id": task_id,
            "elements": [e.to_dict() for e in ir.elements],
            "ambiguous": ir.ambiguous,
            "conflicts": conflicts,
            "summary": summary,
            "warnings": warnings,
        }

    # -- graph query ------------------------------------------------------
    def query_graph(self, task_id: str, element_id: str | None = None,
                    relation_types: List[str] | None = None,
                    depth: int = 1) -> Dict[str, Any]:
        """Return a neighbourhood of the element graph for model reasoning."""
        ir = self._load_ir(task_id)
        if not ir.graph:
            raise AppError(E_INPUT_FILE, f"task has no element graph: {task_id}")
        result = subgraph(ir.graph, element_id, relation_types, depth)
        result["task_id"] = task_id
        return result

    # -- stage 4: plan ----------------------------------------------------
    def plan(self, task_id: str, level_map: Dict[str, float] | None = None,
             family_map: Dict[str, Dict[str, str]] | None = None,
             batch_size: int | None = None) -> Dict[str, Any]:
        ir = self._load_ir(task_id)
        plan = build_plan(ir, self.config, level_map, family_map, batch_size)
        task_dir = self._task_dir(task_id)
        (task_dir / "script" / "plan.json").write_text(
            json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        self.store.update_task(task_id, now_iso(), status="planned")
        return plan

    # -- stage 5: execute -------------------------------------------------
    def execute(self, task_id: str, dry_run: bool = False, resume: bool = True,
                autosave_snapshot: bool = True) -> Dict[str, Any]:
        plan_path = self._task_dir(task_id) / "script" / "plan.json"
        if not plan_path.exists():
            raise AppError(E_INPUT_FILE, f"task has no plan: {task_id}")
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        result = self.executor.execute(task_id, plan, dry_run, resume, autosave_snapshot)
        status = "completed" if result["status"] == "ok" else (
            "partial" if result["status"] == "partial" else "failed")
        self.store.update_task(task_id, now_iso(), status=status)
        return result

    # -- stage 6: check ---------------------------------------------------
    def check(self, task_id: str, tolerance_mm: float = 50.0,
              checks: List[str] | None = None) -> Dict[str, Any]:
        ir = self._load_ir(task_id)
        run_path = self._task_dir(task_id) / "run" / "created.json"
        execution = json.loads(run_path.read_text(encoding="utf-8")) if run_path.exists() else {
            "created": [], "failed": [], "skipped": []}
        verification = verify_model(ir, execution, tolerance_mm, checks)
        (self._task_dir(task_id) / "check" / "check.json").write_text(
            json.dumps(verification, ensure_ascii=False, indent=2), encoding="utf-8")
        return verification

    # -- stage 7: report --------------------------------------------------
    def report(self, task_id: str, template: str = "default", fmt: str = "markdown") -> Dict[str, Any]:
        ir = self._load_ir(task_id)
        task = self.store.get_task(task_id)
        if not task:
            raise AppError(E_INPUT_FILE, f"unknown task: {task_id}")
        parsed_path = self._task_dir(task_id) / "parsed" / "parsed.json"
        parsed = json.loads(parsed_path.read_text(encoding="utf-8")) if parsed_path.exists() else {}
        run_path = self._task_dir(task_id) / "run" / "created.json"
        execution = json.loads(run_path.read_text(encoding="utf-8")) if run_path.exists() else {
            "created": [], "failed": [], "skipped": []}
        check_path = self._task_dir(task_id) / "check" / "check.json"
        verification = json.loads(check_path.read_text(encoding="utf-8")) if check_path.exists() else {
            "checks": {"missing": [], "offset": [], "param": [], "duplicate": []},
            "metrics": {"elements": len(ir.elements), "created": 0, "auto_ratio": 0.0},
        }
        plan_path = self._task_dir(task_id) / "script" / "plan.json"
        plan_stats = json.loads(plan_path.read_text(encoding="utf-8")).get("stats", {}) if plan_path.exists() else {}

        stats = {
            "total": len(ir.elements),
            "mean_confidence": round(sum(e.confidence for e in ir.elements) / len(ir.elements), 2) if ir.elements else 0.0,
            "low_confidence": sum(1 for e in ir.elements if e.confidence < self.config.confidence_threshold),
            "low_confidence_ratio": (sum(1 for e in ir.elements if e.confidence < self.config.confidence_threshold)
                                     / len(ir.elements)) if ir.elements else 0.0,
        }
        ambiguities = self.store.list_ambiguities(task_id)
        markdown = build_report(task_id, {**parsed, "usable": parsed.get("health", {}).get("usable", True)},
                                ir, stats, ambiguities, execution, verification, plan_stats)

        path = self._task_dir(task_id) / "report.md"
        path.write_text(markdown, encoding="utf-8")
        self.store.update_task(task_id, now_iso(), report_path=str(path), status="reported")

        warnings = []
        if fmt != "markdown":
            warnings.append(warning("W-1003", f"暂不支持 {fmt} 导出，已返回 markdown 路径"))
        return {"task_id": task_id, "report_path": str(path), "report": markdown,
                "metrics": verification.get("metrics", {}), "warnings": warnings}


class _NoConverter:
    """Wrapper that forces DXF-only parsing when convert_dwg is false."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self.dwg_converter = ""

    def __getattr__(self, item):
        return getattr(self._config, item)

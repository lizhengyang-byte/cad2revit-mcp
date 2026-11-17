"""Pipeline orchestration used by both the MCP server and the CLI.

One task may hold several drawing files, and one file may hold several plans
(see :mod:`cad2revit_mcp.engine.regions`).  The pipeline therefore works on
*files -> plan regions -> storeys*, and the storey is the unit that gets
detected, graphed, planned and executed.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import Config
from .engine.detector.detect import detect_components
from .engine.detector.rules import load_rules
from .engine.executor import Executor
from .engine.graph import build_graph, cross_floor_analysis, merge_graphs, subgraph
from .engine.parser.dxf_parser import file_hash, now_iso, parse_dxf
from .engine.planner import plan as build_plan
from .engine.regions import Region, build_region_drawing, detect_regions, level_sort_key
from .engine.reporter import build_report
from .engine.resolver import resolve as apply_decisions
from .engine.verifier import verify as verify_model
from .errors import (
    E_INPUT_FILE,
    E_MAPPING_MISSING,
    W_LEVEL_FALLBACK,
    W_PARAM_MISSING,
    AppError,
    warning,
)
from .models import IRDocument, ParsedDrawing, Segment, TextItem, floor_summaries
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


def _combine_digest(digests: List[str]) -> str:
    payload = "".join(sorted(digests))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


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
        """First file of the task (kept for callers that expect one drawing)."""
        drawings = self._load_parsed_set(task_id)
        if not drawings:
            raise AppError(E_INPUT_FILE, f"task not parsed: {task_id}")
        return drawings[0][0]

    def _load_parsed_set(self, task_id: str) -> List[tuple[ParsedDrawing, List[Dict[str, Any]]]]:
        path = self._task_dir(task_id) / "parsed" / "parsed.json"
        if not path.exists():
            raise AppError(E_INPUT_FILE, f"task not parsed: {task_id}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        files = payload.get("files") or [payload]
        result = []
        for entry in files:
            result.append((_parsed_from_json(entry), list(entry.get("regions") or [])))
        return result

    def _load_ir(self, task_id: str) -> IRDocument:
        path = self._task_dir(task_id) / "ir" / "ir.json"
        if not path.exists():
            raise AppError(E_INPUT_FILE, f"task has no IR: {task_id}")
        return IRDocument.from_dict(json.loads(path.read_text(encoding="utf-8")))

    # -- stage 1: parse ---------------------------------------------------
    def parse(self, file_path: str = "", discipline: str = "structure",
              level_name: str = "", convert_dwg: bool = True,
              file_paths: Optional[List[str]] = None) -> Dict[str, Any]:
        """Parse one file, or a set of files that belong to the same task."""
        requested = list(file_paths or [])
        if file_path:  # a single path always comes first when both are given
            requested.insert(0, file_path)
        if not requested:
            raise AppError(E_INPUT_FILE, "no drawing path given")

        paths = []
        for raw in requested:
            path = self.config.resolve_path(raw)
            if not path.exists():
                raise AppError(E_INPUT_FILE, f"file not found: {path.name}")
            paths.append(path)

        digests = [file_hash(path) for path in paths]
        task_id = self._new_task_id(_combine_digest(digests))

        entries: List[Dict[str, Any]] = []
        drawings: List[tuple[ParsedDrawing, List[Region]]] = []
        for path in paths:
            parsed = parse_dxf(path, self.config if convert_dwg else _NoConverter(self.config))
            parsed.task_id = task_id
            regions = detect_regions(parsed, self.rules, source_file=path.name)
            if level_name and len(regions) == 1:
                regions[0].level = level_name
                regions[0].evidence.setdefault("level_from", "argument")
            if not regions:
                regions = [Region(id=f"{path.stem}-R1", bbox=(0, 0, 0, 0),
                                  level=level_name, source_file=path.name)]
            drawings.append((parsed, regions))
            parsed.sheets = [{"id": region.id, "level": region.level,
                              "scale": (parsed.sheets[0].get("scale", "unknown")
                                        if parsed.sheets else "unknown"),
                              "discipline": discipline}
                             for region in regions]

        translated = self._should_translate(drawings)
        for parsed, regions in drawings:
            for region in regions:
                region.confidence = region.confidence or 0.3
        for parsed, regions in drawings:
            entry = _parsed_to_json(parsed)
            entry["regions"] = [region.to_dict() for region in regions]
            entry["translated"] = translated
            entries.append(entry)

        task_dir = self._task_dir(task_id)
        (task_dir / "parsed" / "parsed.json").write_text(
            json.dumps({"task_id": task_id, "files": entries}, ensure_ascii=False, indent=2),
            encoding="utf-8")
        (task_dir / "parsed" / "first_parsed.json").write_text(
            json.dumps(entries[0], ensure_ascii=False, indent=2), encoding="utf-8")

        first = drawings[0][0]
        self.store.create_task(task_id, first.file_name, _combine_digest(digests),
                               f"trace-{task_id}", self.config.rule_set_version, now_iso())
        self.store.register_rule_version(self.config.rule_set_version,
                                         str(self.config.rule_set_version), now_iso())

        all_regions = [region for _, regions in drawings for region in regions]
        return {
            "task_id": task_id,
            "files": [{
                "file": entry["file_name"],
                "stats": {
                    "segments": len(entry["segments"]),
                    "polylines": len(entry["polylines"]),
                    "blocks": len(entry["blocks"]),
                    "texts": len(entry["texts"]),
                },
                "regions": entry["regions"],
            } for entry in entries],
            "sheets": [sheet for _, regions in drawings for sheet in
                       [{"id": region.id, "level": region.level, "elevation_mm": region.elevation_mm,
                         "file": region.source_file} for region in regions]],
            "regions": [region.to_dict() for region in all_regions],
            "layers": entries[0]["layers"],
            "texts": entries[0]["texts"],
            "health": entries[0]["health"],
            "stats": {
                "files": len(entries),
                "regions": len(all_regions),
                "segments": sum(len(entry["segments"]) for entry in entries),
                "polylines": sum(len(entry["polylines"]) for entry in entries),
                "blocks": sum(len(entry["blocks"]) for entry in entries),
                "texts": sum(len(entry["texts"]) for entry in entries),
            },
        }

    @staticmethod
    def _should_translate(drawings: List[tuple[ParsedDrawing, List[Region]]]) -> bool:
        """Re-base coordinates only when more than one plan shares a sheet.

        A single-sheet task keeps the drawing's own coordinates, so existing
        behaviour (and the check-model offset comparison) is unchanged.
        """
        return sum(len(regions) for _, regions in drawings) > 1

    # -- stage 2: detect --------------------------------------------------
    def detect(self, task_id: str, element_types: List[str] | None = None,
               rule_set: str | None = None, confidence_threshold: float | None = None) -> Dict[str, Any]:
        drawings = self._load_parsed_set(task_id)
        threshold = (confidence_threshold if confidence_threshold is not None
                     else self.config.confidence_threshold)
        rules = self.rules
        if rule_set and rule_set != self.config.rule_set_version:
            rules = load_rules(rule_set if rule_set.endswith(".json") else None)

        translate = sum(len(regions) for _, regions in drawings) > 1
        floors: List[Dict[str, Any]] = []
        all_elements: List[Dict[str, Any]] = []
        ambiguities: List[Dict[str, Any]] = []
        seen_levels: Dict[str, int] = {}
        stats_total = {"total": 0, "by_type": {t: 0 for t in ("grid", "column", "wall", "beam")},
                       "low_confidence": 0, "low_confidence_ratio": 0.0}

        floor_index = 0
        for parsed, region_dicts in drawings:
            regions: List[Region] = []
            for raw in region_dicts:
                region = _region_from_dict(raw, parsed.file_name)
                regions.append(region)
            for region in regions:
                floor_index += 1
                level = region.level
                seen_levels[level] = seen_levels.get(level, 0) + 1
                floor_key = level or f"U{floor_index}"
                if level and seen_levels[level] > 1:
                    floor_key = f"{level}-{seen_levels[level]}"

                drawing = build_region_drawing(parsed, region, translate=translate)
                elements, region_ambiguities, stats = detect_components(
                    drawing, rules, threshold, element_types)

                prefix = "" if self._total_floors(drawings) == 1 else f"{floor_key}-"
                for element in elements:
                    if prefix:
                        element.id = f"{prefix}{element.id}"
                        element.source_entities = [f"{prefix}{src}"
                                                   for src in element.source_entities]
                    if level:
                        element.params.setdefault("level", level)
                for item in region_ambiguities:
                    if prefix:
                        item["element_id"] = f"{prefix}{item['element_id']}"
                    item["level"] = level
                if prefix:
                    for element in elements:
                        element.evidence = {**element.evidence, "floor": floor_key}

                element_dicts = [element.to_dict() for element in elements]
                graph = build_graph(element_dicts, (rules or {}).get("graph", {}),
                                    id_prefix="" if self._total_floors(drawings) == 1
                                    else f"F{floor_index}-")
                floor_ambiguities = [
                    {"element_id": item["element_id"], "kind": item["kind"],
                     "detail": item["detail"], "level": level}
                    for item in graph.get("ambiguities", [])
                ]
                ambiguities.extend(region_ambiguities)
                ambiguities.extend(floor_ambiguities)

                conflicts: List[Dict[str, Any]] = []
                if not level:
                    conflicts.append({
                        "code": "level_unassigned",
                        "message": "图纸中未找到楼层标题或标高，无法判断该平面属于哪一层",
                        "region_id": region.id,
                    })
                if level and seen_levels[level] > 1:
                    conflicts.append({
                        "code": "level_duplicate",
                        "message": f"楼层 {level} 在多个平面中重复出现，需确认保留哪一个",
                        "region_id": region.id,
                    })
                if region.evidence.get("duplicate_level") and not any(
                        item["code"] == "level_duplicate" for item in conflicts):
                    conflicts.append({
                        "code": "level_duplicate",
                        "message": f"楼层 {level} 在多个平面中重复出现，需确认保留哪一个",
                        "region_id": region.id,
                    })

                floors.append({
                    "level": level,
                    "floor_key": floor_key,
                    "elevation_mm": region.elevation_mm,
                    "source_file": region.source_file,
                    "region_id": region.id,
                    "bbox": list(region.bbox),
                    "offset": [0.0, 0.0] if not translate else list(region.offset),
                    "counts": {
                        "total": len(elements),
                        "by_type": stats["by_type"],
                        "low_confidence": stats["low_confidence"],
                    },
                    "elements": element_dicts,
                    "graph": graph,
                    "conflicts": conflicts,
                })
                all_elements.extend(element_dicts)
                stats_total["total"] += stats["total"]
                for key, value in stats["by_type"].items():
                    stats_total["by_type"][key] += value
                stats_total["low_confidence"] += stats["low_confidence"]

        if stats_total["total"]:
            stats_total["low_confidence_ratio"] = round(
                stats_total["low_confidence"] / stats_total["total"], 2)
        stats_total["mean_confidence"] = round(
            sum(float(e["confidence"]) for e in all_elements) / len(all_elements), 2
        ) if all_elements else 0.0

        merged = merge_graphs(floors)
        cross = cross_floor_analysis(
            floors, float((rules or {}).get("graph", {}).get("vertical_alignment_tolerance_mm", 100.0)))
        merged["edges"].extend(cross["edges"])
        for issue in cross["issues"]:
            merged["issues"].append(issue)
            merged["ambiguities"].append({
                "element_id": issue["element_id"], "kind": issue["kind"],
                "detail": issue["detail"], "level": issue.get("level"),
            })
            ambiguities.append({"element_id": issue["element_id"], "kind": issue["kind"],
                                "detail": issue["detail"], "level": issue.get("level")})
        merged["metrics"]["cross_floor"] = {"edges": len(cross["edges"]),
                                            "issues": len(cross["issues"])}
        merged["summary"]["headline"] = (
            f"{len(floors)} 层，{merged['metrics']['nodes']} 个构件，"
            f"{merged['metrics']['edges']} 条关系，{merged['metrics']['issues']} 个拓扑异常")

        ordered = sorted(floors, key=lambda f: (level_sort_key(f["level"], f["elevation_mm"])))
        ir = IRDocument(
            task_id=task_id,
            sheet={"level": ordered[0]["level"] if ordered else "",
                   "discipline": "structure", "scale": "unknown",
                   "files": [parsed.file_name for parsed, _ in drawings]},
            elements=[_element_from_dict(e) for e in all_elements],
            ambiguous=[a["element_id"] for a in ambiguities],
            graph=merged,
            floors=ordered,
        )

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
        if stats_total["low_confidence_ratio"] > 0.4:
            warnings.append(warning(W_PARAM_MISSING,
                                    f"低置信度占比 {stats_total['low_confidence_ratio']:.0%}，建议先人工确认再建模"))
        for floor in ordered:
            for conflict in floor.get("conflicts", []):
                if conflict["code"] == "level_unassigned":
                    warnings.append(warning(W_LEVEL_FALLBACK,
                                            f"{floor['region_id']}: 未识别楼层，建模时将回退默认标高"))
                elif conflict["code"] == "level_duplicate":
                    warnings.append(warning(W_PARAM_MISSING, conflict["message"]))

        return {
            "task_id": task_id,
            "elements": all_elements,
            "ambiguous": ambiguities,
            "graph": merged,
            "graph_summary": merged.get("summary", {}),
            "floors": floor_summaries(ir),
            "stats": stats_total,
            "warnings": warnings,
        }

    @staticmethod
    def _total_floors(drawings: List[tuple[ParsedDrawing, List[Region]]]) -> int:
        return sum(len(regions) for _, regions in drawings)

    # -- stage 3: resolve -------------------------------------------------
    def resolve(self, task_id: str, decisions: List[Dict[str, Any]],
                apply_to_similar: bool = False, decided_by: str = "llm") -> Dict[str, Any]:
        ir = self._load_ir(task_id)
        ir, conflicts, summary = apply_decisions(ir, decisions, apply_to_similar, decided_by)

        for decision in decisions:
            if decision.get("element_id") and decision.get("action") in ("accept", "modify", "exclude"):
                self.store.resolve(task_id, decision["element_id"],
                                   decision["action"], decided_by, now_iso())

        self._sync_floors(ir)
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

    @staticmethod
    def _sync_floors(ir: IRDocument) -> None:
        """Keep the per-storey view in step with the flat element list."""
        kept = {element.id: element.to_dict() for element in ir.elements}
        for floor in ir.floors:
            floor["elements"] = [kept[element["id"]] for element in floor.get("elements", [])
                                 if element["id"] in kept]
            floor["counts"]["total"] = len(floor["elements"])

    # -- graph query ------------------------------------------------------
    def query_graph(self, task_id: str, element_id: str | None = None,
                    relation_types: List[str] | None = None, depth: int = 1,
                    floor: str | None = None) -> Dict[str, Any]:
        """Return a neighbourhood of the element graph for model reasoning."""
        ir = self._load_ir(task_id)
        if not ir.graph:
            raise AppError(E_INPUT_FILE, f"task has no element graph: {task_id}")
        graph = ir.graph
        if floor:
            element_ids = {element["id"] for element in
                           next((f["elements"] for f in ir.floors if f["level"] == floor), [])}
            if not element_ids:
                raise AppError(E_INPUT_FILE, f"unknown floor: {floor}")
            graph = {
                **graph,
                "nodes": [node for node in graph.get("nodes", []) if node["id"] in element_ids],
                "edges": [edge for edge in graph.get("edges", [])
                          if edge["source"] in element_ids and edge["target"] in element_ids],
                "issues": [issue for issue in graph.get("issues", [])
                           if issue["element_id"] in element_ids],
            }
        result = subgraph(graph, element_id, relation_types, depth)
        result["task_id"] = task_id
        if floor:
            result["floor"] = floor
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
        parsed_path = self._task_dir(task_id) / "parsed" / "first_parsed.json"
        parsed = json.loads(parsed_path.read_text(encoding="utf-8")) if parsed_path.exists() else {}
        run_path = self._task_dir(task_id) / "run" / "created.json"
        execution = json.loads(run_path.read_text(encoding="utf-8")) if run_path.exists() else {
            "created": [], "failed": [], "skipped": []}
        check_path = self._task_dir(task_id) / "check" / "check.json"
        verification = json.loads(check_path.read_text(encoding="utf-8")) if check_path.exists() else {
            "checks": {"missing": [], "offset": [], "param": [], "duplicate": [], "topology": [],
                       "vertical": []},
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


def _region_from_dict(raw: Dict[str, Any], file_name: str) -> Region:
    from .engine.regions import Region as _Region

    return _Region(
        id=str(raw.get("id", "")),
        bbox=tuple(raw.get("bbox") or (0.0, 0.0, 0.0, 0.0)),
        level=str(raw.get("level", "")),
        level_label=str(raw.get("level_label", "")),
        elevation_mm=raw.get("elevation_mm"),
        evidence=dict(raw.get("evidence") or {}),
        confidence=float(raw.get("confidence", 0.0)),
        segments=list(raw.get("segments") or []),
        polylines=list(raw.get("polylines") or []),
        blocks=list(raw.get("blocks") or []),
        texts=list(raw.get("texts") or []),
        source_file=str(raw.get("source_file") or file_name),
    )


def _element_from_dict(data: Dict[str, Any]):
    from .models import Element

    return Element.from_dict(data)


class _NoConverter:
    """Wrapper that forces DXF-only parsing when convert_dwg is false."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self.dwg_converter = ""

    def __getattr__(self, item):
        return getattr(self._config, item)

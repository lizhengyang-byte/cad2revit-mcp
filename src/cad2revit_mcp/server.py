"""MCP server exposing the seven tools defined in the API specification."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import FastMCP

from .errors import AppError, response
from .pipeline import Pipeline

mcp = FastMCP("cad2revit-mcp")
_pipeline: Optional[Pipeline] = None


def get_pipeline() -> Pipeline:
    global _pipeline
    if _pipeline is None:
        _pipeline = Pipeline()
    return _pipeline


def _fail(exc: AppError, trace_id: str = "") -> Dict[str, Any]:
    return response("error", {}, [], exc.to_dict(), trace_id)


@mcp.tool(name="parse_dwg")
def parse_dwg(file_path: str, discipline: str = "structure", level_name: str = "",
              convert_dwg: bool = True) -> Dict[str, Any]:
    """解析DXF/DWG图纸并做可用性体检，返回图层、文字、图框与健康结论。"""
    try:
        data = get_pipeline().parse(file_path, discipline, level_name, convert_dwg)
        return response("ok", data, [], None, f"trace-{data['task_id']}")
    except AppError as exc:
        return _fail(exc)


@mcp.tool(name="detect_components")
def detect_components(task_id: str, element_types: Optional[List[str]] = None,
                      rule_set: Optional[str] = None,
                      confidence_threshold: Optional[float] = None) -> Dict[str, Any]:
    """识别轴网、柱、墙、梁，输出带证据与置信度的构件IR与疑点清单。"""
    try:
        data = get_pipeline().detect(task_id, element_types, rule_set, confidence_threshold)
        return response("ok", data, data.get("warnings", []), None, f"trace-{task_id}")
    except AppError as exc:
        return _fail(exc, f"trace-{task_id}")


@mcp.tool(name="resolve_ambiguities")
def resolve_ambiguities(task_id: str, decisions: List[Dict[str, Any]],
                        apply_to_similar: bool = False) -> Dict[str, Any]:
    """对低置信度构件执行采纳、修正或排除，并记录裁决依据。"""
    try:
        data = get_pipeline().resolve(task_id, decisions, apply_to_similar)
        return response("ok", data, data.get("warnings", []), None, f"trace-{task_id}")
    except AppError as exc:
        return _fail(exc, f"trace-{task_id}")


@mcp.tool(name="generate_revit_script")
def generate_revit_script(task_id: str, level_map: Optional[Dict[str, float]] = None,
                          family_map: Optional[Dict[str, Dict[str, str]]] = None,
                          batch_size: Optional[int] = None) -> Dict[str, Any]:
    """把构件IR转换为确定性建模指令集，完成族类型映射与标高换算。"""
    try:
        data = get_pipeline().plan(task_id, level_map, family_map, batch_size)
        return response("ok", data, data.get("warnings", []), None, f"trace-{task_id}")
    except AppError as exc:
        return _fail(exc, f"trace-{task_id}")


@mcp.tool(name="run_revit_script")
def run_revit_script(task_id: str, dry_run: bool = False, resume: bool = True,
                     autosave_snapshot: bool = True) -> Dict[str, Any]:
    """在本地Revit按批次建模；dry_run只校验，失败批次整批回滚。"""
    try:
        data = get_pipeline().execute(task_id, dry_run, resume, autosave_snapshot)
        status = "ok" if data["status"] == "ok" else (
            "partial" if data["status"] == "partial" else "error")
        return response(status, data, data.get("warnings", []), None, f"trace-{task_id}")
    except AppError as exc:
        return _fail(exc, f"trace-{task_id}")


@mcp.tool(name="check_model")
def check_model(task_id: str, tolerance_mm: float = 50.0,
                checks: Optional[List[str]] = None) -> Dict[str, Any]:
    """模型与原图比对，输出漏识别、错位、参数缺失与重复构件。"""
    try:
        data = get_pipeline().check(task_id, tolerance_mm, checks)
        return response("ok", data, [], None, f"trace-{task_id}")
    except AppError as exc:
        return _fail(exc, f"trace-{task_id}")


@mcp.tool(name="export_report")
def export_report(task_id: str, template: str = "default", format: str = "markdown") -> Dict[str, Any]:
    """导出建模报告，含体检结论、自动完成比例、疑点裁决、失败明细与待复核清单。"""
    try:
        data = get_pipeline().report(task_id, template, format)
        return response("ok", data, data.get("warnings", []), None, f"trace-{task_id}")
    except AppError as exc:
        return _fail(exc, f"trace-{task_id}")


def run_stdio() -> None:
    mcp.run(transport="stdio")


def list_tool_names() -> List[str]:
    return ["parse_dwg", "detect_components", "resolve_ambiguities",
            "generate_revit_script", "run_revit_script", "check_model", "export_report"]

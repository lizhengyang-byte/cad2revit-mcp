"""Markdown report generation."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List

from ..models import IRDocument, element_title


def _table(headers: List[str], rows: List[List[str]]) -> str:
    if not rows:
        return "_无_"
    line1 = "| " + " | ".join(headers) + " |"
    line2 = "| " + " | ".join(["---"] * len(headers)) + " |"
    body = ["| " + " | ".join(str(cell) for cell in row) + " |" for row in rows]
    return "\n".join([line1, line2, *body])


def build_report(task_id: str, parsed_summary: Dict[str, Any], ir: IRDocument, stats: Dict[str, Any],
                 ambiguities: List[Dict[str, Any]], execution: Dict[str, Any],
                 verification: Dict[str, Any], plan_stats: Dict[str, Any]) -> str:
    created_ids = {item["element_id"] for item in execution.get("created", [])}
    by_type_rows = []
    for element_type in ("grid", "column", "wall", "beam"):
        all_elements = [e for e in ir.elements if e.type == element_type]
        created = [e for e in all_elements if e.id in created_ids]
        by_type_rows.append([
            element_title(element_type),
            str(len(all_elements)),
            str(len(created)),
            str(len(all_elements) - len(created)),
        ])

    ambiguity_rows = [
        [a["element_id"], a["kind"], a.get("decision") or "待确认"]
        for a in ambiguities
    ]

    failure_rows = [
        [f["element_id"], f.get("code", ""), f.get("message", "")]
        for f in execution.get("failed", [])
    ]

    manual: List[str] = []
    for a in ambiguities:
        if not a.get("decision"):
            manual.append(f"待确认构件 {a['element_id']}（{a['kind']}）")
    for item in verification.get("checks", {}).get("missing", []):
        manual.append(f"未建模构件 {item['element_id']}（{item['reason']}）")
    for item in verification.get("checks", {}).get("param", []):
        manual.append(f"参数缺失 {item['element_id']}：{', '.join(item['missing'])}")
    for item in verification.get("checks", {}).get("offset", []):
        manual.append(f"定位偏差 {item['element_id']}：{item['deviation_mm']}mm")
    if not manual:
        manual.append("无，报告可直接归档")

    metrics = verification.get("metrics", {})
    auto_ratio = metrics.get("auto_ratio", 0.0)

    lines = [
        f"# 建模报告 {task_id}",
        "",
        f"生成时间：{datetime.now().isoformat(timespec='seconds')}　"
        f"图纸：{parsed_summary.get('file_name', '')}　"
        f"识别构件：{stats.get('total', 0)}",
        "",
        "## 一、图纸体检结论",
        "",
        f"- 单位换算系数：{parsed_summary.get('unit_scale', 1.0)}",
        f"- 图元统计：线段 {parsed_summary.get('segments', 0)}、"
        f"多段线 {parsed_summary.get('polylines', 0)}、"
        f"图块 {parsed_summary.get('blocks', 0)}、"
        f"文字 {parsed_summary.get('texts', 0)}",
        f"- 体检结论：{'可用' if parsed_summary.get('usable', True) else '不可用'}",
        "",
        "## 二、构件统计与自动完成比例",
        "",
        _table(["构件", "识别数量", "已建模", "未建模"], by_type_rows),
        "",
        f"- 自动完成比例：{auto_ratio:.0%}（已建模 {metrics.get('created', 0)} / 识别 {metrics.get('elements', 0)}）",
        f"- 平均置信度：{stats.get('mean_confidence', 0.0)}　"
        f"低置信度：{stats.get('low_confidence', 0)}（{stats.get('low_confidence_ratio', 0.0):.0%}）",
        f"- 计划构件：{plan_stats.get('planned', 0)}　跳过：{plan_stats.get('skipped', 0)}　"
        f"批次：{plan_stats.get('batches', 0)}",
        "",
        "## 三、疑点与裁决记录",
        "",
        _table(["构件", "疑点类型", "裁决"], ambiguity_rows),
        "",
        "## 四、执行失败明细",
        "",
        _table(["构件", "错误码", "原因"], failure_rows),
        "",
        "## 五、待人工复核清单",
        "",
    ]
    lines.extend(f"{i}. {item}" for i, item in enumerate(manual, 1))
    lines.extend([
        "",
        "> 本报告由工具自动生成。自动完成比例用于衡量节省的绘图工时，不代表模型可直接用于交付；"
        "所有待复核项须由专业人员确认后方可使用。",
    ])
    return "\n".join(lines)

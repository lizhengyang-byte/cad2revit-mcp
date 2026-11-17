# MCP 工具接口

服务名：`cad2revit-mcp`，传输：stdio。所有工具返回同一 envelope。

```json
{
  "status": "ok | partial | error",
  "data": {},
  "warnings": [{"code": "W-1001", "message": "说明"}],
  "error": null,
  "trace_id": "trace-<task_id>"
}
```

工具共八个，调用顺序建议：`parse_dwg` → `detect_components` → （看 `graph_summary`）→ `query_graph` → `resolve_ambiguities` → `generate_revit_script` → `run_revit_script` → `check_model` → `export_report`。

## 1. parse_dwg

解析一张或多张图纸并体检；自动切分平面区域、识别楼层。三种交付形态共用同一入口：

- 单张单层：只传 `file_path`；
- 每层一个文件：`file_paths` 传全部文件（`file_path` 可留空）；
- 一张图多层并排：只传该文件，切分由工具完成。

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `file_path` | string | 条件 | DXF/DWG 绝对路径或工作区相对路径；传了 `file_paths` 时可省略 |
| `file_paths` | string[] | 条件 | 多文件任务的全部图纸路径；与 `file_path` 至少给一个 |
| `discipline` | string | 否 | 默认 `structure` |
| `level_name` | string | 否 | 覆盖识别到的楼层名；单层图纸常用，多楼层时会作为所有区域的默认名 |
| `convert_dwg` | bool | 否 | 默认 true；false 时禁止调用外部转换器 |

```json
{
  "task_id": "t-20261117-abcd",
  "sheets": [{"id": "plan", "level": "2F", "scale": "1:100", "discipline": "structure"}],
  "layers": [{"name": "KZ-框架柱", "LINE": 0, "LWPOLYLINE": 12}],
  "texts": [{"id": "T00001", "content": "KZ1 600x600", "insert": [300, 300]}],
  "health": {"usable": true, "problems": [], "insunits": 4},
  "regions": [{"id": "plan-R1", "level": "2F", "elevation_mm": 3000,
               "bbox": [0, 0, 12000, 9000], "source_file": "plan.dxf",
               "counts": {"segments": 240, "polylines": 12, "blocks": 0, "texts": 38}}],
  "stats": {"segments": 240, "polylines": 12, "blocks": 0, "texts": 38,
            "files": 1, "regions": 1, "levels": ["2F"]}
}
```

`regions` 是切分结论：一张图里并排几层平面就有几条；同一文件里找不到楼层标题时 `level` 为空串，由 `detect_components` 报 `level_unassigned`。多文件任务中每条 `region` 带 `source_file`，用于追溯。

体检问题码：`unit_unknown`、`scale_missing`、`too_few_entities`（直接 `E-1003`）、`exploded`。

## 2. detect_components

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `task_id` | string | 是 | parse_dwg 返回的 id |
| `element_types` | string[] | 否 | 默认四类全开 |
| `rule_set` | string | 否 | 规则文件路径 |
| `confidence_threshold` | number | 否 | 默认 0.75 |

```json
{
  "task_id": "t-20261117-abcd",
  "elements": [{
    "id": "C-001", "type": "column",
    "geometry": {"insert": [0, 0], "section": [600, 600], "rotation": 0},
    "params": {"material": "C30"},
    "evidence": {"layer": "KZ-框架柱", "text": "KZ1 600x600"},
    "confidence": 1.0,
    "source_entities": ["P00001"]
  }],
  "ambiguous": [{"element_id": "B-003", "kind": "no_geometry",
                 "detail": {"reason": "只有梁标注，未找到对应梁线"}}],
  "stats": {"total": 42, "by_type": {"grid": 8, "column": 16, "wall": 12, "beam": 6},
            "mean_confidence": 0.91, "low_confidence": 2, "low_confidence_ratio": 0.05}
}
```

多楼层任务中，`detect_components` 按「文件 → 区域 → 楼层」逐层识别，每层独立成图，最后合并。返回体在单层字段之外增加：

```json
{
  "floors": [{
    "level": "1F", "elevation_mm": 0, "source_file": "总图.dxf", "region_id": "总图-R1",
    "counts": {"total": 10, "by_type": {"grid": 4, "column": 4, "wall": 1, "beam": 1}, "low_confidence": 0},
    "headline": "14 个构件，12 条关系，1 个拓扑异常", "issues": 1, "conflicts": []
  }],
  "graph_summary": {"headline": "4 层，40 个构件，36 条关系，4 个拓扑异常"}
}
```

楼层冲突码：`level_unassigned`（无标题无标高）、`level_duplicate`（同名楼层出现在多个区域/文件）。跨层异常码：`column_misaligned`（上下层柱错位超容差）、`level_gap_unverified`（缺可对位柱网）。多楼层时构件 id 带楼层前缀（`1F-C-001`），层内关系图节点带 `F1-` 命名空间。

疑点类型：识别类 `low_confidence`、`section_conflict`、`section_missing`、`no_geometry`；拓扑类 `not_on_grid`、`beam_unsupported`、`element_overlap`、`isolated_element`；跨层类 `column_misaligned`、`level_gap_unverified`。拓扑与跨层疑点由构件图生成，与识别类疑点合并去重后进入同一份 `ambiguous`，裁决方式完全一致。

返回体同时给出构件图（IR 1.2）：

```json
{
  "graph": {
    "schema": "element-graph/1.0",
    "nodes": [{"id": "C-001", "type": "column", "degree": 2, "grid_position": "A-1/A-2 @ (0.0, 0.0)"},
              {"id": "GI-001", "type": "grid_intersection", "x": 0.0, "y": 0.0}],
    "edges": [{"id": "R-0001", "type": "column_at_intersection", "source": "C-001",
               "target": "GI-001", "weight": 0.0, "unit": "mm"}],
    "issues": [{"kind": "beam_unsupported", "element_id": "B-001"}],
    "metrics": {"nodes": 7, "edges": 3, "grid_intersections": 1, "issues": 3},
    "summary": {"headline": "6 个构件，3 条关系，1 个轴网交点，3 个拓扑异常", "lines": []}
  },
  "graph_summary": {"headline": "...", "lines": ["柱 C-001，锚点 A-1/A-2 @ (0.0, 0.0)，置信度 1.00，关系：位于轴线 A-1（偏移 0.0 mm）；位于交点 A-1/A-2"]}
}
```

阈值与关系语义见 [`graph-model.md`](graph-model.md)。

## 3. resolve_ambiguities

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `task_id` | string | 是 | |
| `decisions` | object[] | 是 | 见下 |
| `apply_to_similar` | bool | 否 | 默认 false；仅在明确要求时开启 |

```json
{
  "decisions": [{
    "element_id": "C-001",
    "action": "modify",
    "params": {"material": "C35"},
    "geometry": {"section": [650, 650]},
    "reason": "结构设计说明第 3.2 条"
  }]
}
```

返回：`summary.applied / excluded / conflicts` 与 `conflicts[]`。裁决不改变未点名构件的任何字段。拓扑与跨层疑点同样用这三个动作裁决：`accept` 表示「图上确实如此，按图建模并人工复核」，`modify` 表示「图纸有误，按修正值建模」，`exclude` 表示「不建该构件」。多楼层时 `floors` 视图会同步裁剪，`plan` 的该层计划数量随之减少。

## 3b. query_graph

按构件、关系类型、邻域深度、楼层查构件图。模型看完图、裁决前使用。

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `task_id` | string | 是 | |
| `element_id` | string | 否 | 起点构件；省略返回整图（大图慎用） |
| `relation_types` | string[] | 否 | 只看指定关系，如 `["beam_end_supported"]` |
| `depth` | int | 否 | 邻域跳数，默认 1 |
| `floor` | string | 否 | 只看该楼层（`1F` / `B2` / `RF`）；先按楼层过滤节点与边，再取子图 |

```json
{
  "schema": "element-graph/1.0", "task_id": "t-20261117-abcd", "floor": "2F",
  "nodes": [{"id": "C-001", "type": "column", "degree": 2}],
  "edges": [{"type": "column_at_intersection", "source": "C-001", "target": "GI-001"}],
  "issues": [], "summary": {"headline": "1 个构件，1 条关系", "lines": []}
}
```

## 4. generate_revit_script

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `task_id` | string | 是 | |
| `level_map` | object | 否 | 覆盖配置，如 `{"4F": 9000}` |
| `family_map` | object | 否 | 覆盖配置，按构件类型给 `family` 与 `type` |
| `batch_size` | int | 否 | 覆盖配置；批次按楼层分组，一层可多批，一批不混层 |

```json
{
  "instructions": [
    {"element_id": "LEVEL:4F", "op": "create_level", "level": "4F",
     "elevation_mm": 9000, "family": "", "type": "", "geometry": {}, "params": {}},
    {"element_id": "C-001", "op": "create_column", "level": "2F",
     "elevation_mm": 3000, "family": "结构柱-混凝土矩形",
     "type": "矩形柱", "geometry": {}, "params": {}, "floor": "2F",
     "relations": [{"type": "column_at_intersection", "target": "GI-001", "weight": 0.0}],
     "issues": []}
  ],
  "batches": [{"batch_no": 1, "floor": "2F", "level": "2F", "elevation_mm": 3000,
               "instruction_hash": "9f2c...", "count": 10, "instructions": []}],
  "floors": [{"level": "2F", "elevation_mm": 3000, "planned": 10, "status": "planned"}],
  "levels": [{"level": "1F", "elevation_mm": 0, "action": "exists"},
             {"level": "4F", "elevation_mm": 9000, "action": "create"},
             {"level": "5F", "elevation_mm": null, "action": "skipped"}],
  "warnings": [{"code": "E-3002", "message": "5F: 无标高可用，整层已跳过"},
               {"code": "W-1004", "message": "W-003: 图层未标注标高，已回退默认 1F"},
               {"code": "W-1005", "message": "B-002: 拓扑异常 beam_unsupported，请复核"}],
  "skipped": [{"element_id": "5F-C-001", "code": "E-3002"}],
  "stats": {"elements": 40, "planned": 40, "skipped": 0, "batches": 4,
            "topology_flagged": 2, "levels_created": 1}
}
```

指令 op：`create_gridline` / `create_column` / `create_wall` / `create_beam` / `create_level`。每条构件指令额外携带 `relations`（该构件的关系）与 `issues`（拓扑异常）与 `floor`，随指令落盘可追溯到图；桥接端不消费 `relations` / `issues`。

`levels[].action` 三态：`exists`（映射表已有）/ `create`（按图纸标高新建）/ `skipped`（无标高可用，整层未建），执行结果与 `auto_ratio` 按此口径统计。

## 5. run_revit_script

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `task_id` | string | 是 | |
| `dry_run` | bool | 否 | true 只校验不创建 |
| `resume` | bool | 否 | 默认 true，跳过已建构件 |
| `autosave_snapshot` | bool | 否 | 默认 true |

```json
{
  "status": "partial", "mode": "bridge",
  "created": [{"element_id": "C-001", "revit_id": "123456", "op": "create_column",
                "level": "2F", "floor": "2F", "position": [0.0, 0.0, 3000.0]}],
  "failed": [{"element_id": "C-002", "code": "E-4003", "message": "族类型未载入"}],
  "skipped": [{"element_id": "C-003", "reason": "already created (idempotent skip)"}],
  "batches": [{"batch_no": 1, "floor": "2F", "success": 10, "failed": 0,
               "instruction_hash": "9f2c..."}],
  "snapshot": {"id": "snap-t-20261117-abcd-153012"},
  "stats": {"created": 41, "failed": 0, "skipped": 0, "batches": 4}
}
```

桥接返回的 `created[]` 必须含 `revit_id`、`position[3]`（mm，取 `LocationPoint`）与 `level`；缺 `position` 时校核器把该构件记为「无法校核」，不当作通过。

## 6. check_model

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `task_id` | string | 是 | |
| `tolerance_mm` | number | 否 | 默认 50 |
| `checks` | string[] | 否 | 默认全部：`missing` `offset` `param` `duplicate` `topology` `vertical` |

```json
{
  "checks": {
    "missing": [{"element_id": "W-004", "type": "wall", "reason": "未在模型中创建"}],
    "offset": [], "param": [], "duplicate": [],
    "topology": [{"element_id": "B-002", "kind": "beam_unsupported",
                  "reason": "梁两端均未落在柱或墙上", "created": true, "position": [0, 5000, 0]}],
    "vertical": [{"element_id": "1F-C-002", "kind": "column_misaligned",
                  "level": "1F", "reason": "上下层柱中心偏差 400 mm，超过 100 mm"}]
  },
  "metrics": {"elements": 40, "created": 41, "auto_ratio": 1.0,
              "problems": 1, "missing": 0, "offset": 0, "param": 0,
              "duplicate": 0, "topology": 0, "vertical": 1}
}
```

`auto_ratio` = 已创建构件数 / 识别构件数（标高指令不进入分母），衡量自动完成比例，不代表可交付。`topology` 与 `vertical` 检查不判断模型对不对，而是判断「建了但图面关系不自洽」的构件有没有被人工看到。

## 7. export_report

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `task_id` | string | 是 | |
| `template` | string | 否 | 默认 `default` |
| `format` | string | 否 | `markdown`（默认）；其他格式返回 `W-1003` |

返回 `report_path` 与 `report` 全文。报告固定六段：图纸体检结论（含**楼层划分**表）、构件统计与自动完成比例（含**按层构件统计**表）、疑点与裁决记录、图关系与拓扑校验（关键关系表 ≤40 条、轴网交点表、拓扑异常表、竖向复核项）、执行失败明细、待人工复核清单。

## 错误码与告警码

| 区间 | 含义 | 典型码 |
| --- | --- | --- |
| `E-100x` | 输入不可用 | `E-1001` 路径越界、`E-1002` 文件不可读/无转换器、`E-1003` 体检不通过 |
| `E-200x` | 识别不确定 | `E-2001` 置信度不足、`E-2002` 证据冲突、`E-2003` 图层未知 |
| `E-300x` | 映射缺失 | `E-3001` 族类型缺失、`E-3002` 标高未知或不存在（该构件或整层被跳过） |
| `E-400x` | 执行失败 | `E-4001` 桥接不可达、`E-4002` 事务失败、`E-4003` API 调用失败 |
| `W-100x` | 告警（仍建模） | `W-1001` 部分失败、`W-1002` 参数或楼层缺失、`W-1003` 报告降级、`W-1004` 标高回退默认值、`W-1005` 拓扑异常 |

`E-3xxx` 表示该构件或该层被跳过、需修映射；`W-10xx` 表示仍然建模、必须复核。`W-1004` 与 `E-3002` 的分工是这一版修掉的语义混淆：前者是「没标高，回退了默认值，建出来了」，后者是「标高不认识，没建」。拓扑与竖向异常（`W-1005`）永不阻断建模，因为它描述的是图纸自身的问题，不是工具的问题。

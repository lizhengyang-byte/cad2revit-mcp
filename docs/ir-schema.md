# IR 数据模型（schema 1.1）

IR（intermediate representation）是识别层与执行层之间唯一的契约。任何字段改名都必须伴随 `schema_version` 升级。

**1.1 相对 1.0 的变化**：新增可选字段 `graph`（构件图，见 [`graph-model.md`](graph-model.md)）；其余字段不变，1.0 的读取方可忽略 `graph` 继续工作。

```json
{
  "schema_version": "1.1",
  "task_id": "t-20261117-abcd",
  "sheet": {"level": "2F", "discipline": "structure", "scale": "1:100"},
  "elements": [],
  "ambiguous": ["C-009"],
  "graph": {
    "schema": "element-graph/1.0",
    "nodes": [], "edges": [], "grid_intersections": [],
    "issues": [],
    "metrics": {"nodes": 6, "edges": 3, "grid_intersections": 1, "isolated": 0, "issues": 3},
    "summary": {"headline": "6 个构件，3 条关系，1 个轴网交点，3 个拓扑异常", "lines": []}
  }
}
```

## Element

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | string | 构件唯一编号，`G-`/`C-`/`W-`/`B-` + 三位序号 |
| `type` | enum | `grid` / `column` / `wall` / `beam` |
| `geometry` | object | 按类型见下表，单位 mm |
| `params` | object | 类型参数，见下表 |
| `evidence` | object | 判定依据：`layer`、`text`、`source`、`block` 等 |
| `confidence` | number | 0–1，两位小数 |
| `source_entities` | string[] | 来源图元 id（线、多段线、文字） |

### geometry 与 params

| type | geometry | params（必填加粗） |
| --- | --- | --- |
| `grid` | `start`、`end` | **name** |
| `column` | `insert`、`section`[宽,高]、`rotation` | **material**、**level** |
| `wall` | `start`、`end`、`thickness` | **thickness**、**level**、material |
| `beam` | `start`、`end`、`width`、`depth` | **width**、**depth**、**level**、material |

`level` 既可写在 `params` 也可由 `sheet.level` 提供；`planner` 优先取构件自身的 `params.level`。

## graph（1.1 新增）

| 字段 | 说明 |
| --- | --- |
| `schema` | 固定 `element-graph/1.0`，与 IR 版本解耦，便于图模型单独演进 |
| `nodes[]` | `id`、`type`（含 `grid_intersection` 伪节点）、`level`、`confidence`、`degree`，柱还会带 `grid_position` |
| `edges[]` | `id`（`R-0001`）、`type`（6 类语义关系）、`source`、`target`、`weight`、`unit`、`evidence` |
| `grid_intersections[]` | `id`、`x`、`y`、`grid_lines`、`grid_names` |
| `issues[]` | `kind`（`not_on_grid` / `beam_unsupported` / `element_overlap` / `isolated_element`）、`element_id`、`detail` |
| `metrics` | 节点/边/交点/孤立/异常计数，按关系类型与楼层分组 |
| `summary` | 面向模型的可读摘要：`headline`、`lines`（一构件一行）、`intersections`、`issue_lines` |

关系类型与阈值的完整定义见 [`graph-model.md`](graph-model.md)；图在链路中的位置见 [`conversion-pipeline.md`](conversion-pipeline.md)。

## 坐标系与单位

- 单位统一为毫米，`unit_scale` 在解析阶段完成换算；
- 坐标与 DXF 世界坐标一致（X 向右、Y 向上），Z 由标高决定；
- 旋转角度单位为度，逆时针为正；
- 进入 Revit 后内部单位为英尺，换算在桥接层完成（`mm / 304.8`），往返均以 mm 对外。

## 状态流转

`parsed → detected → planned → completed / partial / failed → reported`

状态记录在 SQLite `tasks` 表，用于断点续跑与审计。

## 疑点（ambiguous）

`ambiguous` 是 `element_id` 列表，详情在 SQLite `ambiguities` 表：

| 字段 | 说明 |
| --- | --- |
| `kind` | 识别类：`low_confidence` / `section_conflict` / `section_missing` / `no_geometry`；拓扑类：`not_on_grid` / `beam_unsupported` / `element_overlap` / `isolated_element` |
| `detail` | 结构化原因，含阈值、标注原文、几何尺寸、最近轴线等 |
| `decision` | `accept` / `modify` / `exclude`，未裁决为 null |
| `decided_by` / `decided_at` | 裁决来源与时间 |

识别疑点与拓扑疑点共用同一张表、同一个裁决工具，模型不需要学两套流程。

## 兼容性约定

1. 新增可选字段不升版本；删除或重命名字段必须升版本；
2. 执行层遇到未知字段应忽略而不是报错；
3. `id` 在任务内唯一，跨任务不唯一，幂等键包含 `task_id`；
4. 图模型以 `graph.schema` 独立版本化，IR 升级不必然导致图升级。

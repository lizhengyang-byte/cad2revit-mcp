# IR 数据模型（schema 1.0）

IR（intermediate representation）是识别层与执行层之间唯一的契约。任何字段改名都必须伴随 `schema_version` 升级。

```json
{
  "schema_version": "1.0",
  "task_id": "t-20261117-abcd",
  "sheet": {"level": "2F", "discipline": "structure", "scale": "1:100"},
  "elements": [],
  "ambiguous": ["C-009"]
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

## 坐标系与单位

- 单位统一为毫米，`unit_scale` 在解析阶段完成换算；
- 坐标与 DXF 世界坐标一致（X 向右、Y 向上），Z 由标高决定；
- 旋转角度单位为度，逆时针为正。

## 状态流转

`parsed → detected → planned → completed / partial / failed → reported`

状态记录在 SQLite `tasks` 表，用于断点续跑与审计。

## 疑点（ambiguous）

`ambiguous` 是 `element_id` 列表，详情在 SQLite `ambiguities` 表：

| 字段 | 说明 |
| --- | --- |
| `kind` | `low_confidence` / `section_conflict` / `section_missing` / `no_geometry` |
| `detail` | 结构化原因，含阈值、标注原文、几何尺寸等 |
| `decision` | `accept` / `modify` / `exclude`，未裁决为 null |
| `decided_by` / `decided_at` | 裁决来源与时间 |

## 兼容性约定

1. 新增可选字段不升版本；删除或重命名字段必须升版本；
2. 执行层遇到未知字段应忽略而不是报错；
3. `id` 在任务内唯一，跨任务不保证唯一，幂等键包含 `task_id`。
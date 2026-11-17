# 构件图与图关系模型（element-graph/1.0）

## 0. 这份文档回答什么

识别层给出的是**扁平构件清单**。清单能回答「这栋楼有 12 根柱」，回答不了「柱 C-001 在不在 A-1/A-2 交点、它撑着的梁有没有悬空」。大语言模型接手时，缺的正是后者。

构件图把这个缺口显式化：轴线、轴线交点、构件都是节点；交点与构件之间建立带类型、带权重的边；拓扑不自洽处直接升级为疑点，交给 `resolve_ambiguities` 裁决。图随 IR 落盘（IR 1.1 的 `graph` 字段），规划器、校核器、报告与模型读的是同一份结构。

## 1. 两层图

| 层 | 节点 | 作用 |
| --- | --- | --- |
| 轴网层 | 轴线（`grid`）、轴网交点（`grid_intersection` 伪节点） | 定位基准。交点不是构件，但必须作为节点存在，否则「柱在交点上」这条边在子图切片时会被丢掉 |
| 构件层 | 柱、墙、梁 | 被评判对象。节点带 `confidence`、`level`、`degree`，柱另有 `grid_position` |

取舍：交点入节点容易让人误以为它是构件，所以 `type=grid_intersection` 与构件类型显式区分；孤立构件判定必须排除 `grid` 与 `grid_intersection`，否则每条轴线都会被报为孤立。

## 2. 关系词表

| 关系 | 语义 | 权重 | 判定条件 |
| --- | --- | --- | --- |
| `column_on_grid` | 柱中心落在某条轴线上 | 偏距 mm | 点到轴线距离 ≤ `column_grid_tolerance_mm`(250)，投影参数在 [−0.05, 1.05] |
| `column_at_intersection` | 柱中心落在两轴交点 | 偏距 mm | 与交点点位距离 ≤ 250 |
| `wall_follows_grid` | 墙中线沿某条轴线 | 偏距 mm | 方向角差 ≤ 1.5°，偏距 ≤ `wall_grid_offset_tolerance_mm`(200) |
| `beam_end_supported` | 梁端点落在柱/墙上 | 端点距离 mm | 端点到柱心 ≤ `beam_end_tolerance_mm`(600) + 半柱截面；或端点到墙中线 ≤ 300 + 半墙厚 |
| `wall_intersects_column` | 墙中线穿进柱截面 | 距离 mm | 投影在 (0.05, 0.95) 且距离 < 墙厚/2 − `overlap_tolerance_mm`(50) |
| `element_overlap` | 同类构件占据同一位置 | 重叠量 mm | 柱心距 < 0.5×较小柱边 − 50；墙/梁两端点距离 ≤ 4×50 |
| `grid_line_at_intersection` | 交点由哪两条轴线交出 | 0 | 轴网层内部边，不参与构件判定 |
| `same_level` | 同层构件 | — | 保留词。当前不生成边：同层关系制造噪声并让 `degree` 失真，层信息改由节点 `level` 与 `metrics.by_level` 表达 |

阈值来源：代码内 `DEFAULT_GRAPH_RULES` 兜底，`rules/structure_default.json` 的 `graph` 段是工程可调入口。调阈值只改规则文件，不改代码。

## 3. 异常如何进入流程

| 异常 | 触发 | 去向 |
| --- | --- | --- |
| `not_on_grid` | 柱离最近轴线超容差，或图纸无可用轴网 | `graph.issues` + `ambiguous`（`detect_components` 合并去重） |
| `beam_unsupported` | 梁两端都找不到柱/墙支座 | 同上 |
| `element_overlap` | 同类构件重叠（重复画或重复识别） | 同上 |
| `isolated_element` | 构件与任何轴网/构件都没有边（仅构件层判定） | 同上 |

异常表示「图面不自洽」，不表示「这条线不存在」。所以它不进 `errors`，只进 `ambiguous`：模型必须用 `resolve_ambiguities` 给出 `accept` / `modify` / `exclude`，裁决写回 IR 与任务库并在报告里单独成章。这样保证异常被模型看见，而不是被静默吞掉。

## 4. 面向模型的摘要

`graph.summary` 是给模型读的正文；机器读的是 `nodes` / `edges`。约束：

- `headline` 一句话给规模：`6 个构件，3 条关系，1 个轴网交点，3 个拓扑异常`。
- `lines` 一构件一行，顺序确定，格式固定：`柱 C-001，锚点 A-1/A-2 @ (0.0, 0.0)，置信度 1.00，关系：位于轴线 A-1（偏移 0.0 mm）；位于交点 A-1/A-2`。
- 交点在 `lines` 中不单独占行，只作为锚点出现在构件行，避免模型把交点当构件。
- `issue_lines` 一异常一行，供提示词与报告直接引用。

模型行为红线（写进 instructions，不靠模型自觉）：

1. 不得修改 `geometry` 坐标；改位置只能通过 `resolve_ambiguities` 的 `modify`。
2. 带 `issues` 的构件必须显式裁决，不得默认 `accept`。
3. 不得凭空新增轴网交点或轴线；缺轴网就报「图纸缺少轴网」。
4. 关系只用于解释与校验，不得据此脑补图纸未画出的构件（例如「交点处应该有柱」）。
5. 低置信度与拓扑异常同时存在时，先裁决拓扑，再裁决置信度。

## 5. `query_graph` 语义

`query_graph(task_id, element_id?, relation_types?, depth=1)`：

- 不给 `element_id`：整图 + 全部异常。
- 给 `element_id`：从该节点做 `depth` 跳广度扩散，返回节点、两端都在节点集内的边、以及落在这些节点上的异常。
- `relation_types` 在扩散前过滤边。

典型用法：先读 `graph_summary` 找可疑构件，再对可疑构件 `query_graph(depth=2)` 看支座与邻域，最后决定 `accept` / `modify` / `exclude`。

## 6. 已知局限

| 局限 | 影响 | 缓解 |
| --- | --- | --- |
| 只认直线轴网 | 圆楼、斜向轴网会大面积 `not_on_grid` | 报告标注「轴网类型不支持」，交人工 |
| 交点由线段相交求得，容差 50 mm | 轴线画到延长线外会漏交点 | 柱仍可有 `column_on_grid`，只是没有 `column_at_intersection` |
| 墙/梁只比中线不比轮廓 | 厚墙贴边、梁偏轴判不准 | 阈值可调；报告要求复核权重偏大的 `wall_follows_grid` |
| 支座判定用端点距离，不查 Revit 实际连接 | 悬挑梁、构造柱托梁会被判 `beam_unsupported` | 该异常语义是「图上找不到支座」，裁决可 `accept` 并注明原因 |
| 无竖向三维关系 | 柱与上层梁的连接未建图 | `metrics.by_level` 给分层计数，竖向关系留待 1.2 |

## 7. 示例图纸的「脏」是故意的

仓库自带示例图里，墙 W-001 位于 y=2100 而不属于任何轴线，梁 B-001 位于 y=5000 且两端无支座。这不是画错，而是用来演示拓扑检测：自检稳定输出 `beam_unsupported` 与 `isolated_element`，报告第四章会列出。换成干净图纸这两条异常就消失——这正是回归测试要盯住的行为。

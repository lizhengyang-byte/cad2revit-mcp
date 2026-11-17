# 二维转三维的转换链路与接口形态

## 0. 一句话结论

转换不是「把 DWG 丢给模型让它画 Revit」，而是**七跳确定性流水线**：几何归一化 → 规则识别 → 构件图 → 疑点裁决 → 指令规划 → 事务化执行 → 回读校核。模型的职责被压缩为「看图关系、做裁决、写报告」；几何计算与写入模型全部由代码负责。

## 1. 七跳链路

| 跳 | 输入 | 处理 | 输出 | 失败边界 |
| --- | --- | --- | --- | --- |
| 1 解析 | `.dxf` / `.dwg` | ezdxf 读取；DWG 先经外部转换器转 DXF；单位按 `$INSUNITS` 归一化到 mm；线段去重 | `parsed.json`：图层统计、线段、多段线、图块、文字、体检结论 | `E-1002` 读不了、`E-1003` 体检不通过 |
| 2 识别 | `parsed.json` + 规则 JSON | 图层名、几何、文字三路证据打分，输出轴网、柱、墙、梁 | 构件 `elements` + `confidence` + `source_entities` | 低置信度进 `ambiguous`，不中断 |
| 3 建图 | `elements` | 轴网交点、6 类语义边、拓扑异常 | IR 1.1 的 `graph` 段 + `graph_summary` | 建图失败不影响构件本身，异常降级为疑点 |
| 4 裁决 | `ambiguous` + 模型决策 | `accept` / `modify` / `exclude`，可「应用到同类」 | 裁决后的 IR + 决策留痕 | 冲突决策进 `conflicts`，`W-1005` 告警 |
| 5 规划 | IR | 族类型映射、标高换算、批次切分、幂等键、关系随行 | `plan.json`：`instructions` + `batches` | 缺族映射 `E-3001`、缺标高 `E-3002`，跳过该构件 |
| 6 执行 | `batches` | 每批一个 Revit 事务；失败整批回滚；断点续跑靠幂等键 | `created.json`：`revit_id`、`position`、`level` | `E-4001` 桥接不可达、`E-4002` 事务失败、`E-4003` API 异常 |
| 7 校核 | IR + `created.json` | 漏建、错位、参数缺失、重复、拓扑五类比对 | `check.json` + `report.md` | 偏差超容差进报告，不自动改模型 |

## 2. 接口转换的三种形态

| 形态 | 触发 | 谁在转换 | 适用 | 不适用 |
| --- | --- | --- | --- | --- |
| A 数据形态 | `parse_dwg` → `detect_components` | 代码（ezdxf + 规则引擎） | 批量、可复现、需要审计 | 图层混乱、图块被炸开的图纸 |
| B 语义形态 | `detect` 的 `graph` + `resolve_ambiguities` | 大语言模型 | 图纸有歧义、需要专业判断 | 纯机械重复的图纸（模型反而慢且贵） |
| C 写回形态 | `generate_revit_script` → `run_revit_script` | 代码（Add-in + Transaction） | 需要真实模型、需要回滚 | 只想看方案不动模型 |

三者不是三选一，而是同一条链上的三段：A 出几何，B 做判断，C 落模型。让模型直接写 Revit API 调用是此类项目最常见的失败模式——不可复现、不可回滚、无法审计。

## 3. DXF → 几何归一化

| 原始 | 归一化后 | 说明 |
| --- | --- | --- |
| `$INSUNITS=1`（英寸） | 坐标 ×25.4 | 统一到 mm |
| `$INSUNITS=6`（米） | ×1000 | 同上 |
| 闭合 `LWPOLYLINE` / `POLYLINE` | 保留在 `polylines`，拆线后进 `segments` | 闭合轮廓用于柱与墙，拆线用于中线 |
| 重复线段 | 端点 1 mm 内视为同一条，只留一条 | 避免重叠边导致重复构件 |
| 圆弧、样条 | 不参与识别，只在图层统计里计数 | 当前规则仅支持直线型构件 |

## 4. 字段映射：Python（snake_case）→ C#（PascalCase）

桥接契约必须显式，不能依赖反序列化器的大小写不敏感匹配——那会让字段改名静默失效。

| Python | C# | 类型 | 必填 |
| --- | --- | --- | --- |
| `element_id` | `ElementId` | string | 是 |
| `op` | `Op` | string | 是 |
| `level` | `Level` | string | 是 |
| `elevation_mm` | `ElevationMm` | double | 是 |
| `family` | `Family` | string | 是 |
| `type` | `Type` | string | 是 |
| `geometry.start/end/insert/section` | `Geometry.Start/End/Insert/Section` | double[] | 按 `op` |
| `geometry.thickness/width/depth/rotation` | 同名 | double | 按 `op` |
| `params` | `Params` | map | 否 |
| `relations` / `issues` | 不消费（留给审计与报告） | list | 否 |

回传方向：`ExecutionResult` 必须含 `element_id`、`ok`、`revit_id`、`position`（mm，X/Y/Z）、`level`。缺 `position` 会让 `check_model` 的 `offset` 检查永远为空——上一版就是这个缺陷，现改为从 `LocationPoint` 回读并四舍五入到 0.1 mm，取不到时回退到指令起点。

## 5. 事务边界与回滚半径

- 一批 = 一个 `Transaction`，默认批 80 条（`batch_size` 可配）。
- 批内任一构件失败 → 整批 `RollBack`，批内其余构件标记 `E-4002` 已回滚；已成功的前序批次保留。
- 因此回滚半径是「一批」，不是「整个任务」。整任务回滚会让 5000 构件的图纸无法分阶段交付。
- 续跑：每批有 `instruction_hash`，每个构件有幂等键；重跑时命中的构件直接 `skipped`。

## 6. 幂等键

```
key = sha256(element_id + geometry + rule_version)[:16]
```

几何先递归 `round(0.1)` 归一化再哈希：`599.99999` 与 `600.0` 必须得到同一个键，否则浮点抖动会让同一构件被创建两次。这是上一版直接哈希原始浮点留下的缺陷。

## 7. 失败与重试矩阵

| 错误 | 是否重试 | 处理 |
| --- | --- | --- |
| `E-4001` 桥接不可达 | 是（启动 Revit 后） | 保留任务，幂等键保证不重复 |
| `E-4002` 事务回滚 | 是（修正失败构件后） | 只重跑失败批次 |
| `E-4003` API 抛错 | 一次 | 仍失败则升级人工 |
| `E-3001` 族类型未载入 | 否 | 在 Revit 载入族后重跑该批 |
| `E-3002` 标高不在映射表 | 否 | 补 `level_map` 后重新 `plan` |

## 8. 验收口径

| 指标 | 口径 | 合格线 |
| --- | --- | --- |
| 自动完成比例 | 已建模构件 / 识别构件 | 示例图 1.00；工程图目标 ≥0.85 |
| 定位偏差 | 回读 `position` 与 IR 坐标的欧氏距离 | ≤ 容差（默认 50 mm） |
| 拓扑一致性 | `graph.issues` 全部有裁决 | 未裁决数 = 0 |
| 参数完整 | `required_params` 全部有值 | 缺失数进报告 |
| 可复算 | 同图纸 + 同规则 → 同 IR、同计划、同幂等键 | 除时间戳外逐字节一致 |

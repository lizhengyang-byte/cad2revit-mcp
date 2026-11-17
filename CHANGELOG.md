# 变更记录

## 0.3.0 (2026-11-17)

多楼层能力：一张图、多张图、一张图里多层并排都在同一条链路上处理。

- 新增：`engine/regions.py`——平面切分。标题识别（`1F/一层/首层/地下二层/屋顶层/F3`，归一化为 `1F/B2/RF`）与空白带投影递归切分双轨；标题 ≥2 时按最近标题分配（可处理多层共用外框），标题不足时按空白带切；非平面小块并入主区域。区域持久化成员索引，支持回切。
- 新增：标高解析支持 `±0.000` / `3.300` / `标高 -5.400` / 裸米制短数值（毫米制图纸中不可能作为尺寸）。
- 新增：IR schema 1.2，`floors[]` 楼层视图（`level`、`elevation_mm`、`region_id`、`counts`、`graph`、`headline`、`conflicts`）；`sheet`/`elements` 保留为兼容视图。
- 新增：`parse_dwg` 支持 `file_paths` 多文件任务；多区域时每层换算到自身左下角局部原点。
- 新增：`detect_components` 按「文件→区域→楼层」逐层识别，构件 id 多楼层时带层前缀（`1F-C-001`）；层内独立建图（`id_prefix=F{i}-`），合并后保留每条 headline 控上下文。
- 新增：跨层关系 `column_stacks_on`（容差 100mm，可配置）与异常 `column_misaligned`、`level_gap_unverified`。
- 新增：`generate_revit_script` 按层解析标高（映射表 → 图纸标高 → 整层 skip），缺层时生成 `create_level` 指令；批次按层分组，一批不混层，回滚半径等于一层。
- 新增：`check_model` 的 `vertical` 检查（上下层柱错位、缺层冲突），报告新增「楼层划分」表与竖向复核项。
- 新增：`query_graph` 的 `floor` 参数。
- 新增：Revit Add-in 支持 `create_level`；`FindLevel` 改为严格按名匹配，找不到报 `E-3002`，不再「取标高最接近」静默错层。
- 新增：`samples.build_multi_floor_drawing` / `build_floor_file` / `add_plan_block` 示例生成器。
- 新增：`tests/test_regions.py`、`tests/test_multifloor.py`（13 个新用例，总计 41）。
- 文档：新增 `docs/multi-storey.md`；更新 `architecture.md`、`conversion-pipeline.md`、`ir-schema.md`、`mcp-tools.md`、README。

## 0.2.2 (2026-11-17)

许可证变更：MIT → Apache License 2.0。

- 替换：`LICENSE` 全文改为 Apache-2.0，新增 `NOTICE`（版权归属与第三方依赖许可）
- 更新：`pyproject.toml` 的 `license` 与 Trove classifier 同步改为 Apache-2.0；README 的 License 章节说明选择理由（专利授权与商标限制）
- 影响：已发布的 0.1.0–0.2.1 仍按 MIT 授权；自本版起的新代码与分发按 Apache-2.0

## 0.2.1 (2026-11-17)

安装可用性修复（由「真实 pip install 后跑不通」暴露）。

- 修复：`mcp>=1.0` 未设上限，pip 会装到 2.x，而 2.x 已把 FastMCP 改名为 MCPServer，服务启动即 `ModuleNotFoundError`；依赖改为 `mcp>=1.0,<2`
- 修复：`--selftest` 依赖 `scripts.make_sample_dxf`，非仓库目录安装后必然失败；示例生成器移入 `cad2revit_mcp.samples`，`scripts/` 仅保留 CLI 包装
- 修复：规则文件只存在于仓库根 `rules/`，非 editable 安装读不到；规则随包分发（`src/cad2revit_mcp/rules/`），加载时包内优先、仓库兜底，并加用例防止两份副本漂移
- 新增：`tests/test_packaging.py`（规则打包、副本一致、示例生成器可导入）
- 验证：`pip install -e .` 后 `--tools` 列出 8 个工具，stdio 握手 `initialize` 成功，`tools/call parse_dwg` 返回 ok

## 0.2.0 (2026-11-17)

构件图层与转换链路规格。

- 新增：`engine/graph.py` 构件图层——轴网交点入节点、6 类语义边（`column_on_grid` / `column_at_intersection` / `wall_follows_grid` / `beam_end_supported` / `wall_intersects_column` / `element_overlap` / `grid_line_at_intersection`）、4 类拓扑异常（`not_on_grid` / `beam_unsupported` / `element_overlap` / `isolated_element`）
- 新增：IR schema 1.1，新增 `graph` 字段（图自身版本 `element-graph/1.0`），`detect_components` 同时返回 `graph` 与 `graph_summary`
- 新增：第 8 个 MCP 工具 `query_graph`，按构件 / 关系类型 / 邻域深度取子图
- 新增：拓扑异常并入 `ambiguous`，复用既有裁决链路，报告新增「图关系与拓扑校验」章节（五段变六段）
- 新增：`check_model` 增加 `topology` 检查；`generate_revit_script` 指令随行携带 `relations` 与 `issues`
- 新增：`rules/structure_default.json` 的 `graph` 阈值段；新增 `docs/conversion-pipeline.md`、`docs/graph-model.md`
- 修复：路径白名单用字符串前缀判断，`/workspace-evil` 可绕过 `/workspace`，改为 `Path.relative_to` 包含判定（`config.is_allowed`）
- 修复：幂等键直接哈希浮点几何，`599.99999` 与 `600.0` 生成不同键，改为递归 `round(0.1)` 后再哈希
- 修复：桥接只回 `revit_id` 不回坐标，真实 Revit 下 `check_model` 的 `offset` 永远为空，改为回读 `LocationPoint` 并回传 `position` / `level`
- 修复：DWG 转换器整串当作 `argv[0]`，无法带参数，改用 `shlex.split`
- 修复：标高回退默认值混用 `E-3002`（与「标高缺失跳过」同码），新增 `W-1004` 表达「已回退默认标高」
- 已知限制：Revit Add-in 仍未在真实项目验收；图关系仅覆盖水平投影关系

## 0.1.0 (2026-11-17)

首个可运行版本。

- 新增：DXF 解析与单位归一（mm）、图纸体检（单位缺失/比例缺失/图元过少/散线）
- 新增：轴网、柱、墙、梁四类构件规则识别，输出证据与置信度
- 新增：疑点裁决（accept / modify / exclude），只影响点名构件
- 新增：本地模拟执行与 Revit HTTP 桥接执行，幂等键去重、批次事务、快照记录
- 新增：确定性指令生成、批次划分、指令哈希、族与标高映射校验
- 新增：模型回验（漏识别/错位/参数缺失/重复）与五段式报告
- 新增：7 个 MCP 工具与统一返回体
- 已知限制：DWG 需外部转换器；不支持 OCR 与扫描图纸；Revit Add-in 仅为参考实现

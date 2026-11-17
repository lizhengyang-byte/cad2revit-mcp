# 变更记录

## 0.2.2 (2026-11-17)

许可证变更：MIT → Apache License 2.0。

- 替换：`LICENSE` 全文改为 Apache-2.0，新增 `NOTICE`（版权归属与第三方依赖许可）
- 更新：`pyproject.toml` 按 PEP 639 改为 `license = "Apache-2.0"` + `license-files`（随 wheel 打包 LICENSE/NOTICE），移除已弃用的 License classifier
- 更新：README 的 License 章节说明选择理由（专利授权与商标限制）
- 影响：已发布的 0.1.0–0.2.1 仍按 MIT 授权；自本版起的新代码与分发按 Apache-2.0

## 0.2.1 (2026-11-17)

安装可用性修复（由「真实 pip install 后跑不通」暴露）。

- 修复：`mcp>=1.0` 未设上限，pip 会装到 2.x，而 2.x 已把 FastMCP 改名为 MCPServer，服务启动即 `ModuleNotFoundError`；依赖改为 `mcp>=1.0,<2`
- 修复：`--selftest` 依赖 `scripts.make_sample_dxf`，非仓库目录安装后必然失败；示例生成器移入 `cad2revit_mcp.samples`，`scripts/` 仅保留 CLI 包装
- 修复：规则文件只存在于仓库根 `rules/`，非 editable 安装读不到；规则随包分发（`src/cad2revit_mcp/rules/`），加载时包内优先、仓库兜底，并加用例防止两份副本漂移
- 修复：`__version__` 写死为 0.1.0，包版本与命令输出长期不一致；改为读安装元数据
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
- 新增：确定性指令生成、批次划分、指令哈希、族与标高映射校验
- 新增：本地模拟执行与 Revit HTTP 桥接执行，幂等键去重、批次事务、快照记录
- 新增：模型回验（漏识别/错位/参数缺失/重复）与五段式报告
- 新增：7 个 MCP 工具与统一返回体
- 已知限制：DWG 需外部转换器；不支持 OCR 与扫描图纸；Revit Add-in 仅为参考实现

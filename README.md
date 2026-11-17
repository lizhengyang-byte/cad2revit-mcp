# cad2revit-mcp

把二维结构 CAD 图纸转换成 Revit 三维模型的 MCP 工具。定位是**让大语言模型通过 MCP 做「二维翻三维」的翻模**：模型负责看图、判断和处置疑点，工具负责确定性的几何处理、规则识别与建模执行。

> 现状：`0.3.0`。新增**多楼层**能力：单张图纸、每层一个文件、一张图纸里多层平面并排，都由工具自己切分与识别，不需要用户预先拆图，也不套模板。识别与执行链路可端到端跑通，执行层以「本地模拟 + Revit 参考 Add-in」两种形态提供，**尚未在真实 Revit 项目上完成验收**。请勿直接用于交付级生产。

## 它解决什么问题

结构专业从 CAD 平面图翻到 Revit，重复劳动集中在四件事：看图找柱墙梁、拄截面尺寸、对齐轴网标高、逐个建构件。这个工具把这段工作拆成可校验的流水线：

```
DXF/DWG → 切分楼层与区域 → 逐层规则识别(证据+置信度) → 构件图(轴网锚点/支座/重叠/上下层对位)
        → 疑点裁决 → 按层分批生成指令 → Revit 执行 → 回验 → 报告
```

其中「看楼层、看图关系、做裁决」交给大语言模型通过 MCP 工具调用完成；「切分—识别—指令—执行—回验」是纯确定性代码，保证同样输入得到同样结果。图关系与阈值见 [`docs/graph-model.md`](docs/graph-model.md)，每一跳的接口形态见 [`docs/conversion-pipeline.md`](docs/conversion-pipeline.md)，多楼层切分与冲突口径见 [`docs/multi-storey.md`](docs/multi-storey.md)。

### 楼层怎么处理

| 交付形态 | 调用方式 | 工具行为 |
| --- | --- | --- |
| 一张图一层 | `parse_dwg(file_path=...)` | 单区域，行为与 0.2 完全一致 |
| 每层一个文件 | `parse_dwg(file_paths=[...])` | 逐文件切分，合并为多楼层任务 |
| 一张图多层并排 | `parse_dwg(file_path=...)` | 按平面标题做最近分配；标题不足时按空白带投影递归切分 |

切分只认两类证据：平面标题（`1F/一层/首层/地下二层/屋顶层/F3`）与平面间的空白带，不认图框与图层命名，因此不需要用户先把图纸整理成模板。楼层名、标高、冲突（`level_unassigned` / `level_duplicate`）、层间柱位偏差（`column_misaligned`）都作为结构化事实返回，模型据此判断而不是靠看图猜测。

## 能力边界（先看这里）

| 支持 | 不支持 |
| --- | --- |
| 结构专业：轴网、柱、墙、梁 | 建筑装修、机电、钢筋、节点详图 |
| DXF（内置解析） | DWG 需自备转换器（ODA/AutoCAD CLI），否则 `E-1002` |
| 图纸内文字标注（TEXT/MTEXT） | 扫描件、图片、PDF 图纸；OCR 属后续版本 |
| 单张/多张/一张并排的图纸，自动切分楼层 | 需要用户先按模板拆图才能用的方案 |
| 逐构件证据与置信度、人工裁决 | 全自动交付、无人值守批量出图 |
| 层内拓扑关系与上下层柱位对齐校验 | 真实传力路径、板与洞口、节点详图 |
| 本地模拟执行 + Revit 桥接参考实现 | 独立 UI（本工具是 MCP 服务，由客户端承载界面） |

## 安装

需要 Python 3.10+。

```bash
git clone https://github.com/lizhengyang-byte/cad2revit-mcp.git
cd cad2revit-mcp
python -m venv .venv && source .venv/bin/activate
pip install -e .
```

依赖：`mcp(<2)`、`ezdxf`、`shapely`。`mcp` 2.x 把 `FastMCP` 改名为 `MCPServer`，本版仍用 1.x API，因此依赖上限锁在 `<2`；升级到 2.x 需改 `server.py` 的导入与实例化。

## 快速跑通（不需要 Revit）

```bash
# 生成示例图纸并跑完整流程
PYTHONPATH=src python -m cad2revit_mcp --selftest --workspace ./workspace

# 生成四层并排总图并跑完整流程（多楼层采样）
PYTHONPATH=src python -m cad2revit_mcp --selftest --workspace ./workspace --multi

# 查看对外暴露的工具（应为八个）
PYTHONPATH=src python -m cad2revit_mcp --tools

# 启动 MCP 服务（stdio）
PYTHONPATH=src python -m cad2revit_mcp
```

自检会在 `./workspace/tasks/<task_id>/` 下留下 `parsed/`、`ir/`、`script/`、`run/`、`check/`、`report.md`，可直接打开报告看识别结果、楼层划分与图关系。

## 接到 MCP 客户端

```json
{
  "mcpServers": {
    "cad2revit": {
      "command": "python",
      "args": ["-m", "cad2revit_mcp"],
      "env": {
        "PYTHONPATH": "/absolute/path/to/cad2revit-mcp/src",
        "CAD2REVIT_WORKSPACE": "/absolute/path/to/workspace"
      }
    }
  }
}
```

## 八个工具

| 工具 | 作用 | 关键约束 |
| --- | --- | --- |
| `parse_dwg` | 解析图纸（`file_path` 或 `file_paths`），切分楼层，输出图层、文字、区域与体检结论 | 只读；路径必须在白名单内 |
| `detect_components` | 逐层识别轴网/柱/墙/梁，输出 IR、构件图、楼层视图与疑点 | 每个构件带 `evidence` 与 `confidence` |
| `query_graph` | 按构件/关系类型/邻域深度/楼层查构件图 | 先看图关系，再裁决 |
| `resolve_ambiguities` | 对疑点执行 accept / modify / exclude | 只改点名构件，不「改一个动一片」 |
| `generate_revit_script` | 按层生成确定性指令集，缺层发 `create_level`，分批并计算哈希 | 标高、族类型缺失即跳过并告警 |
| `run_revit_script` | 按批执行，失败批次整批回滚 | 幂等键去重，重复执行不重复建模 |
| `check_model` | 与原图比对，查漏、错位、缺参、重复、拓扑、竖向对位 | 默认容差 50mm、竖向 100mm |
| `export_report` | 导出六段式建模报告（含楼层划分表） | 目前仅 markdown |

统一返回体：

```json
{
  "status": "ok | partial | error",
  "data": {},
  "warnings": [{"code": "W-1001", "message": "..."}],
  "error": null,
  "trace_id": "trace-t-20261117-abcd"
}
```

完整入参、出参与数据模型见 [`docs/mcp-tools.md`](docs/mcp-tools.md) 与 [`docs/ir-schema.md`](docs/ir-schema.md)。

## 构件图：让模型看懂「关系」而不是只看到「构件」

`detect_components` 除构件列表外，还建一张构件图：轴线、轴网交点、柱、墙、梁都是节点，边带类型与数值。

```json
{
  "summary": {"headline": "6 个构件，3 条关系，1 个轴网交点，3 个拓扑异常",
              "lines": ["柱 C-001，锚点 A-1/A-2 @ (0.0, 0.0)，置信度 1.00，关系：位于轴线 A-1（偏移 0.0 mm）；位于交点 A-1/A-2"]}
}
```

| 关系 | 含义 | 发现的问题 |
| --- | --- | --- |
| `column_at_intersection` | 柱落在轴网交点 | 不在交点 → `not_on_grid` |
| `beam_end_supported` | 梁端落在柱/墙 | 两端都无支座 → `beam_unsupported` |
| `wall_follows_grid` | 墙沿轴线 | 自由墙需复核 |
| `element_overlap` | 同类构件重叠 | 重复识别 |
| `wall_intersects_column` | 墙穿柱 | 建模错误 |
| `column_stacks_on` | 上下层柱投影对位（跨层） | 超容差 → `column_misaligned` |
| `grid_continues` | 轴网层间延续（跨层） | 无可对位柱网 → `level_gap_unverified` |

拓扑异常不代表工具出错，而是图纸本身有问题，因此只告警（`W-1005`）并把构件编号交回模型与人工，不阻断建模；同时它们会并入 `ambiguous`，强制模型显式裁决。跨层关系同理：多楼层时只保留每层一行 headline，避免几百个构件的说明挤爆上下文。

## 架构

```
src/cad2revit_mcp/
├── server.py            # 八个 MCP 工具
├── pipeline.py          # 解析→切分→识别→建图→裁决→规划→执行→回验→报告
├── models.py            # IR（1.2，含 floors）与几何数据结构
├── config.py            # 白名单、阈值、族与标高映射
├── errors.py            # 错误码与统一返回体
├── store.py             # SQLite：任务、疑点、批次、幂等键
├── geometry.py          # 平行、重叠、闭合、面积等纯函数
├── samples.py           # 单层与多楼层示例图纸生成
└── engine/
    ├── parser/          # ezdxf 解析与单位归一
    ├── regions.py       # 平面切分：标题 + 空白带 → 楼层与标高
    ├── detector/        # 规则库 + 四类构件识别与置信度
    ├── graph.py         # 构件图：轴网交点、层内关系、跨层对位、摘要
    ├── resolver.py      # 裁决落库与 IR 更新（含 floors 同步）
    ├── planner.py       # 按层标高解析、建层指令、按层分批、关系随行
    ├── executor.py      # Revit 桥接 / 本地模拟、幂等、快照
    ├── verifier.py      # 模型回验（含拓扑与竖向对位）
    └── reporter.py      # 六段式报告（含楼层划分）
```

设计取舍与数据流见 [`docs/architecture.md`](docs/architecture.md)。

## 识别是怎么做的

规则而不是「猜」：

1. **图层语义**：图层名正则匹配（`KZ`/`QT`/`KL`/`AXIS`/`轴线` 等），命中记 `layer_weight`。
2. **几何约束**：柱看闭合多段线或图块包围盒；墙看平行线间距（60–400mm）与重叠长度；梁看梁线长度与标注距离；轴网看贯穿长度。
3. **文字证据**：就近取标注，解析 `600x600`、`250x600` 这类截面；标注与几何矛盾时扣分并生成疑点。
4. **置信度**：`0.5×图层 + 0.3×几何 + 0.2×标注`，冲突扣 `0.25`。默认阈值 `0.75`，低于阈值的构件全部进疑点清单，不会静默建模。

规则库是可替换的 JSON：[`rules/structure_default.json`](rules/structure_default.json)。构件图阈值在同文件的 `graph` 段，楼层切分参数在 `regions` 段。

## Revit 侧

`revit_addin/` 是一份 C# 参考实现：在 Revit 里起本地 HTTP 端点，接收批次指令，一个 Transaction 内创建构件，失败整批回滚，回传 `revit_id`、`position`（mm，从 `LocationPoint` 回读）与 `level`。编译见 [`revit_addin/README.md`](revit_addin/README.md)。

投入真实项目前必须注意两点：`position` 是校核的前提，没有它就查不了错位；标高必须严格按名匹配（`create_level` 负责新建，同名则复用），找不到时直接报 `E-3002`——旧版「取最接近标高」会在图纸写 `4F` 而模型只有 `3F` 时静默建到错层。

## 配置

```json
{
  "workspace_dir": "./workspace",
  "allowed_paths": ["./workspace", "/path/to/drawings"],
  "confidence_threshold": 0.75,
  "review_threshold": 0.9,
  "batch_size": 80,
  "rule_set_version": "structure-2026.11",
  "default_level": "1F",
  "bridge_url": "http://127.0.0.1:8787",
  "dwg_converter": "",
  "family_map": {
    "column": {"family": "结构柱-混凝土矩形", "type": "矩形柱"}
  },
  "level_map": {"1F": 0.0, "2F": 3000.0, "3F": 6000.0}
}
```

`dwg_converter` 指向可执行文件，签名为 `<converter> input.dwg output.dxf`；允许带参数（如 `ODAFileConverter --recurse`），内部按 `shlex` 切分，不经 shell。`level_map` 只是「已知楼层表」，不在表中的楼层如果图纸给得出标高，planner 会发 `create_level` 而不是回退。

## 测试

```bash
python -m pytest -q
ruff check src tests
```

41 个用例覆盖解析、四类构件识别、构件图（交点锚定/支座/孤立/子图切片/确定性）、区域切分（单张/并排/共用图框/无标题/局部坐标）、多文件合并、跨层对位与错位告警、按层分批与建层指令、幂等执行、回验（含竖向检查）、路径白名单与 IR 往返。

## 路线图

- `0.1`：结构四类构件、DXF、规则识别、本地模拟执行、报告
- `0.2`：构件图与拓扑校验、IR 1.1、`query_graph`、转换链路规格、安全与幂等修复
- `0.3`（当前）：多楼层切分与识别、IR 1.2、按层分批与 `create_level`、竖向对位校验、严格标高匹配
- `0.4`：Revit Add-in 实测与族模板校验、DWG 转换链路、圆弧/斜向轴网
- `0.5`：OCR 标注、梁跨与竖向传力关系、批量图纸队列
- 之后：建筑与机电专业、模型 diff

## 贡献

见 [`CONTRIBUTING.md`](CONTRIBUTING.md)。提交前请确保 `pytest` 与 `ruff` 全绿，并说明规则改动对置信度与图关系的影响。

## 免责声明

本工具输出的是**待复核的建模草稿**。结构计算结果、构件尺寸、连接关系必须由具备资质的工程师复核，不得直接作为施工依据。作者不对使用本工具造成的任何工程后果承担责任。

## License

Apache License 2.0，见 [`LICENSE`](LICENSE)，署名与第三方依赖许可见 [`NOTICE`](NOTICE)。

选择 Apache-2.0 而不是 MIT，主要为了**明确的专利授权条款**（第 3 条）与**商标使用限制**（第 6 条）：这类涉及 CAD 解析与 Revit API 桥接的工具，贡献者可能来自设计院或软件厂商，专利授权写得清楚比「宽松」更重要。

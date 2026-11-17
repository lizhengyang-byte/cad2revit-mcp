# cad2revit-mcp

把二维结构 CAD 图纸转换成 Revit 三维模型的 MCP 工具。定位是**让大语言模型通过 MCP 做"二维翻三维"的翻模**：模型负责看图、判断和处置疑点，工具负责确定性的几何处理、规则识别与建模执行。

> 现状：`0.1.0` 早期版本。识别与执行链路可端到端跑通，执行层以"本地模拟 + Revit 参考 Add-in"两种形态提供，**尚未在真实 Revit 项目上完成验收**。请勿直接用于交付级生产。

## 它解决什么问题

结构专业从 CAD 平面图翻到 Revit，重复劳动集中在四件事：看图找柱墙梁、抄截面尺寸、对齐轴网标高、逐个建构件。这个工具把这段工作拆成可校验的流水线：

```
DXF/DWG → 解析 → 规则识别(带证据+置信度) → 疑点裁决 → 指令生成 → Revit 执行 → 回验 → 报告
```

其中"识别—裁决"环节交给大语言模型通过 MCP 工具调用完成，"指令生成—执行—回验"是纯确定性代码，保证同样的输入得到同样的结果。

## 能力边界（先看这里）

| 支持 | 不支持 |
| --- | --- |
| 结构专业：轴网、柱、墙、梁 | 建筑装修、机电、钢筋、节点详图 |
| DXF（内置解析） | DWG 需自备转换器（ODA/AutoCAD CLI），否则报 `E-1002` |
| 图纸内文字标注（TEXT/MTEXT） | 扫描件、图片、PDF 图纸；OCR 属后续版本 |
| 逐构件证据与置信度、人工裁决 | 全自动交付、无人值守批量出图 |
| 本地模拟执行 + Revit 桥接参考实现 | 独立 UI（本工具是 MCP 服务，由客户端承载界面） |

## 安装

需要 Python 3.10+。

```bash
git clone https://github.com/lizhengyang-byte/cad2revit-mcp.git
cd cad2revit-mcp
python -m venv .venv && source .venv/bin/activate
pip install -e .
```

依赖：`mcp`、`ezdxf`、`shapely`。

## 快速跑通（不需要 Revit）

```bash
# 生成示例图纸并跑完整流程
PYTHONPATH=src python -m cad2revit_mcp --selftest --workspace ./workspace

# 查看对外暴露的工具
PYTHONPATH=src python -m cad2revit_mcp --tools

# 启动 MCP 服务（stdio）
PYTHONPATH=src python -m cad2revit_mcp
```

自检会在 `./workspace/tasks/<task_id>/` 下留下 `parsed/`、`ir/`、`script/`、`run/`、`check/`、`report.md`，可以直接打开报告看识别结果。

## 接到 MCP 客户端

在支持 stdio 的 MCP 客户端里加一段配置：

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

## 七个工具

| 工具 | 作用 | 关键约束 |
| --- | --- | --- |
| `parse_dwg` | 解析图纸，输出图层、文字、图框与体检结论 | 只读；路径必须在白名单内 |
| `detect_components` | 识别轴网/柱/墙/梁，输出 IR 与疑点 | 每个构件都带 `evidence` 与 `confidence` |
| `resolve_ambiguities` | 对疑点执行 accept / modify / exclude | 只改点名构件，不"改一个动一片" |
| `generate_revit_script` | 生成确定性建模指令集，分批次并计算哈希 | 族类型、标高缺失即跳过并告警 |
| `run_revit_script` | 按批执行，失败批次整批回滚 | 幂等键去重，重复执行不会重复建模 |
| `check_model` | 与原图比对，查漏、错位、缺参、重复 | 默认容差 50mm |
| `export_report` | 导出五段式建模报告 | 目前仅 markdown |

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

## 架构

```
src/cad2revit_mcp/
├── server.py            # 七个 MCP 工具
├── pipeline.py          # 解析→识别→裁决→规划→执行→回验→报告
├── models.py            # IR 与几何数据结构
├── config.py            # 白名单、阈值、族与标高映射
├── errors.py            # 错误码与统一返回体
├── store.py             # SQLite：任务、疑点、批次、幂等键
├── geometry.py          # 平行、重叠、闭合、面积等纯函数
└── engine/
    ├── parser/          # ezdxf 解析与单位归一
    ├── detector/        # 规则库 + 四类构件识别与置信度
    ├── resolver.py      # 裁决落库与 IR 更新
    ├── planner.py       # 指令生成、批次、映射校验
    ├── executor.py      # Revit 桥接 / 本地模拟、幂等、快照
    ├── verifier.py      # 模型回验
    └── reporter.py      # 报告生成
```

设计取舍与数据流见 [`docs/architecture.md`](docs/architecture.md)。

## 识别是怎么做的

规则而不是"猜"：

1. **图层语义**：图层名正则匹配（`KZ`/`QT`/`KL`/`AXIS`/`轴线` 等），命中记 `layer_weight`。
2. **几何约束**：柱看闭合多段线或图块的包围盒；墙看两条平行线的间距（60–400mm）与重叠长度；梁看梁线长度与标注距离；轴网看贯穿长度。
3. **文字证据**：就近取标注，解析 `600x600`、`250x600` 这类截面；标注与几何矛盾时扣分并生成疑点。
4. **置信度**：`0.5×图层 + 0.3×几何 + 0.2×标注`，冲突扣 `0.25`。默认阈值 `0.75`，低于阈值的构件全部进疑点清单，不会静默建模。

规则库是可替换的 JSON：[`rules/structure_default.json`](rules/structure_default.json)。

## Revit 侧

`revit_addin/` 提供一份 C# 参考实现：在 Revit 里起一个本地 HTTP 端点，接收批次指令，在一个 Transaction 内创建构件，失败整批回滚，并把每个构件的 Revit ElementId 回传。编译方式见 [`revit_addin/README.md`](revit_addin/README.md)。

把 `config.bridge_url`（或环境里的配置）指向该端点后，`run_revit_script` 会自动切到 `bridge` 模式；未配置时走 `local` 模拟模式，便于在没有 Revit 的机器上测试识别链路。

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

`dwg_converter` 指向一个可执行文件，签名为 `<converter> input.dwg output.dxf`。

## 测试

```bash
python -m pytest -q
```

16 个用例覆盖解析、四类构件识别、批次规划、幂等执行、回验、路径白名单与 IR 往返。

## 路线图

- `0.1`（当前）：结构四类构件、DXF、规则识别、本地模拟执行、报告
- `0.2`：Revit Add-in 实测与族模板校验、DWG 转换链路、更多墙梁规则
- `0.3`：OCR 标注、梁跨与支座识别、批量图纸队列
- 之后：建筑与机电专业、模型对比 diff

## 贡献

见 [`CONTRIBUTING.md`](CONTRIBUTING.md)。提交前请确保 `pytest` 全绿，并说明规则改动对置信度的影响。

## 免责声明

本工具输出的是**待复核的建模草稿**。结构计算结果、构件尺寸、连接关系必须由具备资质的工程师复核，不得直接作为施工依据。作者不对使用本工具造成的任何工程后果承担责任。

## License

MIT，见 [`LICENSE`](LICENSE)。

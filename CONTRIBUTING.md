# 贡献指南

## 开发环境

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest -q
```

## 提交前检查

1. `python -m pytest -q` 全绿；
2. 新增识别规则时，在 `rules/structure_default.json` 里给出可解释的图层正则或尺寸区间，并补一个用合成 DXF 的用例；
3. 修改置信度权重必须在 PR 描述里说明理由，以及受影响构件类型；
4. 不改动 `docs/ir-schema.md` 约定的字段名，除非同步更新 schema 版本。

## 代码约定

- 识别层只做"看见什么"，不做"应该是什么"的业务判断；
- 执行层只接受确定性指令，不读图、不做推断；
- 任何写操作必须幂等，重复执行不能产生重复构件；
- 不新增删除类操作（本项目只写不删）。

## PR 描述模板

```
## 改了什么
## 为什么
## 对识别结果的影响（哪个构件类型、置信度如何变化）
## 如何验证
```

# 示例图纸

`make_sample_dxf.py` 会生成一张最小可用的结构平面图，用于自检、测试和演示。

```bash
PYTHONPATH=src python scripts/make_sample_dxf.py samples/sample_structure.dxf
```

图纸内容：

| 图层 | 图元 | 对应构件 |
| --- | --- | --- |
| `AXIS-轴网` | 两条正交长线（10000mm） | 2 根轴网 |
| `KZ-框架柱` | 两个 600×600 闭合多段线 + 标注 `KZ1 600x600` | 2 根柱 |
| `QT-墙` | 两条间距 200mm 的平行线（6000mm） | 1 道墙 |
| `KL-框架梁` | 一条 6000mm 梁线 + 标注 `KL2 250x600` | 1 根梁 |
| `DIM-标注` | 10 条短尺寸线 | 仅用于通过图元数量体检 |

预期识别结果：`grid=2, column=2, wall=1, beam=1`，平均置信度约 `0.9`。

`.dxf` 已在 `.gitignore` 中排除，`samples/*.dxf` 例外；示例文件不随仓库提交，运行脚本即可生成。

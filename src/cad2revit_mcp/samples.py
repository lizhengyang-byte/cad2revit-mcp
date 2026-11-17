"""Generate a small structural sample DXF for demos and self tests.

Lives inside the package (not ``scripts/``) so ``cad2revit-mcp --selftest``
works after a plain ``pip install``, without a checkout on ``PYTHONPATH``.
"""

from __future__ import annotations

from pathlib import Path

import ezdxf

LAYERS = {
    "AXIS-轴网": 3,
    "KZ-框架柱": 1,
    "QT-墙": 2,
    "KL-框架梁": 5,
    "TEXT-标注": 7,
    "DIM-标注": 8,
}


def build_sample(path: Path) -> Path:
    """Write the reference sample drawing and return its path.

    The drawing is deliberately imperfect: the wall sits at y=2100 (not on an
    axis) and the beam has no support at either end.  That is what makes
    ``--selftest`` prove the topology checks still fire.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 4  # millimetres
    for name, color in LAYERS.items():
        if name not in doc.layers:
            doc.layers.add(name, color=color)

    msp = doc.modelspace()

    # Grid: two axes crossing at the origin
    msp.add_line((0, 0), (0, 10000), dxfattribs={"layer": "AXIS-轴网"})
    msp.add_line((0, 0), (10000, 0), dxfattribs={"layer": "AXIS-轴网"})

    # Columns: two 600x600 closed polylines with section labels
    for cx, cy, label in ((0, 0, "KZ1 600x600"), (6000, 0, "KZ2 600x600")):
        msp.add_lwpolyline(
            [(cx - 300, cy - 300), (cx + 300, cy - 300),
             (cx + 300, cy + 300), (cx - 300, cy + 300)],
            close=True,
            dxfattribs={"layer": "KZ-框架柱"},
        )
        msp.add_text(label, dxfattribs={"layer": "TEXT-标注", "height": 200}
                     ).set_placement((cx + 400, cy + 400))

    # Wall: two parallel lines 200 mm apart (off-axis on purpose)
    msp.add_line((0, 2000), (6000, 2000), dxfattribs={"layer": "QT-墙"})
    msp.add_line((0, 2200), (6000, 2200), dxfattribs={"layer": "QT-墙"})

    # Beam: one beam line with a section label (unsupported on purpose)
    msp.add_line((0, 5000), (6000, 5000), dxfattribs={"layer": "KL-框架梁"})
    msp.add_text("KL2 250x600", dxfattribs={"layer": "TEXT-标注", "height": 200}
                 ).set_placement((3000, 5150))

    # Dimension-like filler so the drawing passes the minimum entity check
    for i in range(10):
        y = 7000 + i * 150
        msp.add_line((0, y), (1200, y), dxfattribs={"layer": "DIM-标注"})

    doc.saveas(str(path))
    return path

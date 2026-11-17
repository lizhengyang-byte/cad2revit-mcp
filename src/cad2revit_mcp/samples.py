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


PLAN_LAYERS = {
    "AXIS-轴网": 3,
    "KZ-框架柱": 1,
    "QT-墙": 2,
    "KL-框架梁": 5,
    "TEXT-标注": 7,
    "DIM-标注": 8,
}


def add_plan_block(msp, ox: float, oy: float, title: str, elevation: str = "",
                   label_prefix: str = "", bay: float = 6000.0,
                   column_shift: tuple[float, float] = (0.0, 0.0)) -> None:
    """Draw one structural plan at (ox, oy): 2 axes, 4 columns, wall, beam.

    ``title`` is the plan title text ("1F 一层平面"); ``elevation`` is an
    optional elevation note drawn under the title ("±0.000").
    """
    for offset in (0.0, bay):
        msp.add_line((ox + offset, oy - 1500), (ox + offset, oy + bay + 3000),
                     dxfattribs={"layer": "AXIS-轴网"})
        msp.add_line((ox - 1500, oy + offset), (ox + bay + 3000, oy + offset),
                     dxfattribs={"layer": "AXIS-轴网"})
    for dx in (0.0, bay):
        for dy in (0.0, bay):
            cx, cy = ox + dx + column_shift[0], oy + dy + column_shift[1]
            msp.add_lwpolyline(
                [(cx - 300, cy - 300), (cx + 300, cy - 300),
                 (cx + 300, cy + 300), (cx - 300, cy + 300)],
                close=True, dxfattribs={"layer": "KZ-框架柱"})
            msp.add_text(f"{label_prefix}KZ 600x600",
                         dxfattribs={"layer": "TEXT-标注", "height": 200}
                         ).set_placement((cx + 400, cy + 400))
    msp.add_text(title, dxfattribs={"layer": "TEXT-标注", "height": 350}
                 ).set_placement((ox, oy + bay + 3500))
    if elevation:
        msp.add_text(elevation, dxfattribs={"layer": "TEXT-标注", "height": 250}
                     ).set_placement((ox + 2500, oy + bay + 3500))
    # wall along the lower axis, but offset so it is a real wall pair
    msp.add_line((ox, oy + 2100), (ox + bay, oy + 2100), dxfattribs={"layer": "QT-墙"})
    msp.add_line((ox, oy + 2300), (ox + bay, oy + 2300), dxfattribs={"layer": "QT-墙"})
    # beam spanning the columns with a section label
    msp.add_line((ox, oy + bay), (ox + bay, oy + bay), dxfattribs={"layer": "KL-框架梁"})
    msp.add_text("KL1 250x600", dxfattribs={"layer": "TEXT-标注", "height": 200}
                 ).set_placement((ox + bay / 2, oy + bay + 150))
    for i in range(12):
        y = oy + 7000 + i * 150
        msp.add_line((ox, y), (ox + 1200, y), dxfattribs={"layer": "DIM-标注"})


def build_multi_floor_drawing(path: Path, floors: int = 4,
                              spacing: float = 20000.0) -> Path:
    """One DXF holding several plans laid out side by side (the hard case)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 4
    for name, color in PLAN_LAYERS.items():
        if name not in doc.layers:
            doc.layers.add(name, color=color)
    msp = doc.modelspace()
    for index in range(1, floors + 1):
        elevation = f"{((index - 1) * 3.0):.3f}"
        add_plan_block(msp, (index - 1) * spacing, 0.0,
                       f"{index}F {index}层平面", elevation=elevation,
                       label_prefix=f"{index}F-")
    doc.saveas(str(path))
    return path


def build_floor_file(path: Path, index: int, origin_x: float = 0.0) -> Path:
    """One DXF per storey - the classic multi-file delivery."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 4
    for name, color in PLAN_LAYERS.items():
        if name not in doc.layers:
            doc.layers.add(name, color=color)
    msp = doc.modelspace()
    add_plan_block(msp, origin_x, 0.0, f"{index}F {index}层平面",
                   elevation=f"{((index - 1) * 3.0):.3f}", label_prefix=f"{index}F-")
    doc.saveas(str(path))
    return path

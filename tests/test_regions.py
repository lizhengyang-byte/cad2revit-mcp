"""Region splitting: one sheet, side-by-side plans, and shared frames."""

from pathlib import Path

import ezdxf

from cad2revit_mcp.config import Config
from cad2revit_mcp.engine.detector.rules import load_rules
from cad2revit_mcp.engine.parser.dxf_parser import parse_dxf
from cad2revit_mcp.engine.regions import (
    build_region_drawing,
    detect_regions,
    parse_elevation,
    parse_level_title,
)
from cad2revit_mcp.pipeline import Pipeline
from cad2revit_mcp.samples import (
    PLAN_LAYERS,
    add_plan_block,
    build_floor_file,
    build_multi_floor_drawing,
    build_sample,
)


def _parsed(path, config):
    return parse_dxf(Path(path), config)


def test_single_sheet_is_one_region(tmp_path, config):
    parsed = _parsed(build_sample(tmp_path / "single.dxf"), config)
    regions = detect_regions(parsed, load_rules())
    assert len(regions) == 1, "单张平面图不应被切成多块"
    assert regions[0].level == "", "示例图没有楼层标题，不应凭空猜一个楼层"
    assert len(regions[0].segments) == len(parsed.segments), "所有线段都应归入该区域"


def test_side_by_side_plans_get_one_region_each(tmp_path, config):
    parsed = _parsed(build_multi_floor_drawing(tmp_path / "four.dxf", 4), config)
    regions = detect_regions(parsed, load_rules())

    assert len(regions) == 4
    assert [region.level for region in regions] == ["1F", "2F", "3F", "4F"]
    for region in regions:
        assert region.bbox[2] - region.bbox[0] < 15000, "区域宽度应是一张平面，而不是整张图"
        assert len(region.segments) >= 15

    # membership must not leak between neighbouring plans
    owner = {}
    for index, region in enumerate(regions):
        for segment in region.segments:
            assert segment not in owner, "同一线段不应属于两个区域"
            owner[segment] = index
    assert len(owner) == len(parsed.segments)


def test_shared_frame_still_splits_by_titles(tmp_path, config):
    path = tmp_path / "framed.dxf"
    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 4
    for name, color in PLAN_LAYERS.items():
        doc.layers.add(name, color=color)
    msp = doc.modelspace()
    add_plan_block(msp, 0.0, 0.0, "1F 一层平面", elevation="0.000", label_prefix="1F-")
    add_plan_block(msp, 20000.0, 0.0, "2F 二层平面", elevation="3.000", label_prefix="2F-")
    # one outer frame around both plans: whitespace splitting would fail here
    msp.add_lwpolyline([(-2000, -2000), (30000, -2000), (30000, 11000), (-2000, 11000)],
                       close=True, dxfattribs={"layer": "DIM-标注"})
    doc.saveas(str(path))

    parsed = _parsed(path, config)
    regions = detect_regions(parsed, load_rules())
    assert len(regions) == 2, "共用一个图框时也要按平面标题切分"
    assert [region.level for region in regions] == ["1F", "2F"]


def test_untitled_plans_split_by_whitespace_and_flag_themselves(tmp_path, config):
    path = tmp_path / "untitled.dxf"
    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 4
    for name, color in PLAN_LAYERS.items():
        doc.layers.add(name, color=color)
    msp = doc.modelspace()
    add_plan_block(msp, 0.0, 0.0, "结构平面", label_prefix="A-")
    add_plan_block(msp, 20000.0, 0.0, "结构平面", label_prefix="B-")
    doc.saveas(str(path))

    pipeline = Pipeline(Config(workspace_dir=tmp_path, allowed_paths=[tmp_path]))
    parsed = pipeline.parse(str(path))
    detected = pipeline.detect(parsed["task_id"])
    assert len(detected["floors"]) == 2, "无标题也应按空白带切成两处平面"
    assert all(floor["level"] == "" for floor in detected["floors"])
    assert all(floor["conflicts"] for floor in detected["floors"]), "缺楼层必须成为显式冲突"


def test_region_drawing_is_rebased_to_local_origin(tmp_path, config):
    parsed = _parsed(build_multi_floor_drawing(tmp_path / "four.dxf", 3), config)
    regions = detect_regions(parsed, load_rules())
    third = regions[2]
    drawing = build_region_drawing(parsed, third, translate=True)
    xs = [point[0] for segment in drawing.segments for point in (segment.start, segment.end)]
    assert min(xs) <= 1.0, "多层并排时每层都应换算到自己的局部原点"


def test_level_and_elevation_vocabulary():
    assert parse_level_title("1F 一层平面") == "1F"
    assert parse_level_title("二层平面") == "2F"
    assert parse_level_title("首层") == "1F"
    assert parse_level_title("地下二层") == "B2"
    assert parse_level_title("屋顶层") == "RF"
    assert parse_level_title("KZ1 600x600") is None
    assert parse_elevation("±0.000") == 0.0
    assert parse_elevation("3.300") == 3300.0
    assert parse_elevation("标高 -5.400") == -5400.0
    assert parse_elevation("600x600") is None


def test_multi_file_task_merges_storeys(tmp_path):
    pipeline = Pipeline(Config(workspace_dir=tmp_path, allowed_paths=[tmp_path]))
    first = build_floor_file(tmp_path / "1F.dxf", 1)
    second = build_floor_file(tmp_path / "2F.dxf", 2)
    parsed = pipeline.parse("", file_paths=[str(first), str(second)])
    assert parsed["stats"]["files"] == 2
    detected = pipeline.detect(parsed["task_id"])
    assert [floor["level"] for floor in detected["floors"]] == ["1F", "2F"]
    assert detected["stats"]["total"] == 20

"""Entry point: run the MCP server over stdio."""

from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from .config import Config
from .pipeline import Pipeline
from .server import list_tool_names, run_stdio


def _selftest(config: Config, multi: bool = False) -> int:
    from .samples import build_multi_floor_drawing, build_sample

    if multi:
        sample = config.workspace_dir / "selftest_multi.dxf"
        build_multi_floor_drawing(sample, floors=4)
    else:
        sample = config.workspace_dir / "selftest.dxf"
        build_sample(sample)
    pipeline = Pipeline(config)
    parsed = pipeline.parse(str(sample))
    detected = pipeline.detect(parsed["task_id"])
    planned = pipeline.plan(parsed["task_id"])
    executed = pipeline.execute(parsed["task_id"], dry_run=False)
    checked = pipeline.check(parsed["task_id"])
    report = pipeline.report(parsed["task_id"])
    summary = {
        "task_id": parsed["task_id"],
        "elements": detected["stats"],
        "batches": planned["stats"]["batches"],
        "execute": executed["status"],
        "auto_ratio": checked["metrics"]["auto_ratio"],
        "report": report["report_path"],
    }
    if multi:
        summary["floors"] = [
            {"level": floor.get("level"), "elevation_mm": floor.get("elevation_mm"),
             "total": (floor.get("counts") or {}).get("total")}
            for floor in detected.get("floors", [])
        ]
        summary["levels"] = planned.get("levels", [])
        summary["vertical"] = checked["metrics"].get("vertical", 0)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="cad2revit-mcp")
    parser.add_argument("--config", help="path to config.json")
    parser.add_argument("--workspace", help="override workspace directory")
    parser.add_argument("--tools", action="store_true", help="print tool names and exit")
    parser.add_argument("--selftest", action="store_true", help="run an end-to-end self test")
    parser.add_argument("--multi", action="store_true",
                        help="with --selftest: use the side-by-side multi-storey sample")
    parser.add_argument("--version", action="version", version=f"cad2revit-mcp {__version__}")
    args = parser.parse_args()

    if args.workspace:
        import os
        os.environ["CAD2REVIT_WORKSPACE"] = args.workspace
    config = Config.load(args.config)

    if args.tools:
        print("\n".join(list_tool_names()))
        return 0
    if args.selftest:
        return _selftest(config, multi=args.multi)

    run_stdio()
    return 0


if __name__ == "__main__":
    sys.exit(main())

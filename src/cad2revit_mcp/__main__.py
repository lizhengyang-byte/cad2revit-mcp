"""Entry point: run the MCP server over stdio."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .config import Config
from .pipeline import Pipeline
from .server import list_tool_names, run_stdio


def _selftest(config: Config) -> int:
    from scripts.make_sample_dxf import build_sample

    sample = config.workspace_dir / "selftest.dxf"
    build_sample(sample)
    pipeline = Pipeline(config)
    parsed = pipeline.parse(str(sample))
    detected = pipeline.detect(parsed["task_id"])
    planned = pipeline.plan(parsed["task_id"])
    executed = pipeline.execute(parsed["task_id"], dry_run=False)
    checked = pipeline.check(parsed["task_id"])
    report = pipeline.report(parsed["task_id"])
    print(json.dumps({
        "task_id": parsed["task_id"],
        "elements": detected["stats"],
        "batches": planned["stats"]["batches"],
        "execute": executed["status"],
        "auto_ratio": checked["metrics"]["auto_ratio"],
        "report": report["report_path"],
    }, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="cad2revit-mcp")
    parser.add_argument("--config", help="path to config.json")
    parser.add_argument("--workspace", help="override workspace directory")
    parser.add_argument("--tools", action="store_true", help="print tool names and exit")
    parser.add_argument("--selftest", action="store_true", help="run an end-to-end self test")
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
        return _selftest(config)

    run_stdio()
    return 0


if __name__ == "__main__":
    sys.exit(main())

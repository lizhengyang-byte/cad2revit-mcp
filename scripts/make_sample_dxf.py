"""Generate a small structural sample DXF for demos and tests.

Thin CLI wrapper; the generator itself lives in ``cad2revit_mcp.samples`` so
that ``cad2revit-mcp --selftest`` works from an installed package.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from cad2revit_mcp.samples import build_sample


if __name__ == "__main__":  # pragma: no cover
    parser = argparse.ArgumentParser(description="generate a sample structural DXF")
    parser.add_argument("output", nargs="?", default="samples/sample_structure.dxf")
    args = parser.parse_args()
    print(build_sample(Path(args.output)))

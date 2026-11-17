import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cad2revit_mcp.config import Config  # noqa: E402
from cad2revit_mcp.pipeline import Pipeline  # noqa: E402
from scripts.make_sample_dxf import build_sample  # noqa: E402


@pytest.fixture()
def workspace(tmp_path):
    return tmp_path


@pytest.fixture()
def config(tmp_path):
    cfg = Config(workspace_dir=tmp_path, allowed_paths=[tmp_path])
    return cfg


@pytest.fixture()
def sample_path(tmp_path):
    return build_sample(tmp_path / "sample_structure.dxf")


@pytest.fixture()
def pipeline(config, sample_path):
    p = Pipeline(config)
    parsed = p.parse(str(sample_path))
    p.detect(parsed["task_id"])
    return p, parsed["task_id"]

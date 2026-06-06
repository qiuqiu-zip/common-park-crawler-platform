from __future__ import annotations

import shutil
import uuid
from pathlib import Path

import pytest


@pytest.fixture
def workspace_tmp_path():
    path = Path("test-output") / uuid.uuid4().hex
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)
    return path


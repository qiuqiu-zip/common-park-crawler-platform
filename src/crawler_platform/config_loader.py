from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import SpiderConfig
from .validation import dump_spider_config, ensure_valid_spider_config, validate_spider_config


def load_spider_config(path: str | Path) -> SpiderConfig:
    config_path = Path(path)
    data = _load_mapping(config_path)
    return ensure_valid_spider_config(data)


__all__ = ["dump_spider_config", "load_spider_config", "validate_spider_config"]


def _load_mapping(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in {".yaml", ".yml"}:
        try:
            import yaml
        except Exception as exc:
            raise RuntimeError("YAML config requires PyYAML; use JSON or install PyYAML.") from exc
        return yaml.safe_load(text)
    return json.loads(text)

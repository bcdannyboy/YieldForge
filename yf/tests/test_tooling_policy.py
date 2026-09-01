from __future__ import annotations

import hashlib
import tomllib
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
AUDIT_NOTEBOOK = "notebooks/m11-economic-resolution.ipynb"
AUDIT_NOTEBOOK_SHA256 = "e40e73d0da4e22e4d723c9604cf6fd129e4690b93d80f57e43c93988bd71beba"


def test_ruff_excludes_only_the_content_addressed_audit_notebook() -> None:
    config = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert config["tool"]["ruff"].get("extend-exclude") == [AUDIT_NOTEBOOK]
    assert hashlib.sha256((PROJECT_ROOT / AUDIT_NOTEBOOK).read_bytes()).hexdigest() == (
        AUDIT_NOTEBOOK_SHA256
    )

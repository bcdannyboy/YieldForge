from __future__ import annotations

import tomllib
from pathlib import Path

YF_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = YF_ROOT.parent


def test_sdist_has_explicit_public_source_boundary() -> None:
    metadata = tomllib.loads((YF_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    sdist = (
        metadata.get("tool", {})
        .get("hatch", {})
        .get("build", {})
        .get("targets", {})
        .get("sdist", {})
    )

    assert sdist, "Hatch must define an explicit source-distribution policy"
    assert set(sdist.get("include", [])) == {"/pyproject.toml", "/src"}

    required_excludes = {
        "/.pytest_cache",
        "/.ruff_cache",
        "/dist",
        "/native/**/target",
        "/var",
        "/web/.playwright-browsers",
        "/web/dist",
        "/web/node_modules",
        "/web/playwright-report",
        "/web/test-results",
        "**/*.py[cod]",
        "**/__pycache__",
    }
    assert required_excludes <= set(sdist.get("exclude", []))


def test_root_ignore_protects_generated_distribution_and_raw_m11_results() -> None:
    rules = {
        line.strip()
        for line in (REPOSITORY_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    required_rules = {
        "yf/dist/",
        "yf/experiments/results/m11-gate2-*.json",
        "yf/experiments/results/m11-gate3-early-*.json",
    }
    assert required_rules <= rules

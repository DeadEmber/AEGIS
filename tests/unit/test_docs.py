"""Docs generated from code must be in sync; mandatory documents must exist."""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MANDATORY = ["README.md", "AGENTS.md", "CHANGELOG.md", "SECURITY.md", ".env.example",
             "docs/ARCHITECTURE.md", "docs/ROADMAP.md", "docs/DATA_DICTIONARY.md", "docs/FEATURE_CATALOG.md",
             "docs/MODEL_REGISTRY.md", "docs/STRATEGY_SPECIFICATION.md", "docs/BACKTEST_METHODOLOGY.md",
             "docs/RISK_MANAGEMENT.md", "docs/DEPLOYMENT.md", "docs/TESTING.md", "docs/COST_ANALYSIS.md",
             "docs/STATUS.md"]


def _gen():
    spec = importlib.util.spec_from_file_location("gen_docs", ROOT / "scripts" / "gen_docs.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_mandatory_documents_exist():
    missing = [p for p in MANDATORY if not (ROOT / p).exists()]
    assert not missing, missing


def test_generated_sections_up_to_date():
    gen = _gen()
    for path, body in (("docs/FEATURE_CATALOG.md", gen.features_md()), ("docs/DATA_DICTIONARY.md", gen.data_md())):
        text = (ROOT / path).read_text(encoding="utf-8")
        assert body.strip() in text, f"{path} is stale: run python scripts/gen_docs.py"

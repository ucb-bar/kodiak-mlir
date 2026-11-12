"""Kodiak serves its spatial state-manifest reader to Merlin through a declared plugin."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_contract_declares_the_spatial_reader():
    contract = yaml.safe_load((ROOT / "contracts/target_contract.yaml").read_text())
    assert contract["plugin"]["spatial_introspect"] == "spatial_introspect.py"


def test_spatial_reader_exposes_the_plugin_interface():
    from merlin.targetgen.plugins import load_declared

    reader = load_declared("kodiak", "spatial_introspect")
    assert reader.ARC_TARGETS
    for name in ("build_fact_bundle", "render_fact_bundle", "spatial_facts"):
        assert callable(getattr(reader, name)), name

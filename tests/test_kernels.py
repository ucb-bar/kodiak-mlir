"""The hand-written OPU kernels in kernels/: the registry, the adapter's hand-off, the header facts.

The kernels themselves are checked by Merlin's kernel-test on spike's OPU model
(examples/kodiak/run.sh kernel-test <name>); these checks need no hardware sources.
"""

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def adapter(monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("native launch"))
    monkeypatch.setenv("MERLIN_TARGET_PATH", str(ROOT))
    from merlin.targetgen.plugins import load_declared

    return load_declared("kodiak", "matrix_lowering")


def test_the_registry_names_the_generated_default_and_the_tile_loop():
    from merlin.compiler.selection import kernels as MK

    reg = MK.load_registry(ROOT)
    assert reg.header == "saturn_opu.h" and reg.header_prefix == "OPU"
    assert reg.default_for("saturn_opu").generated
    loop = MK.resolve("opu_tile_loop", package_root=ROOT, unit="saturn_opu")
    assert loop.function == "opu_tile_loop_gemm" and loop.entry.constraints.any_shape and loop.sha256
    assert not (ROOT / "kernels" / reg.header).exists(), "the header is generated at build time, never committed"


def test_a_selected_kernel_is_handed_to_the_shim_and_none_changes_nothing(adapter, monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(adapter.opu_shim, "build_object", lambda *a, **k: calls.append(k) or "built")
    common = dict(unit="u", config="c", cc="cc", cflags=[], scalar_tile=False, parallel_tiles=False)
    adapter.build_object({}, tmp_path, **common)
    assert "kernel" not in calls[-1]
    choice = SimpleNamespace(name="mine")
    adapter.build_object({}, tmp_path, **common, kernel=choice)
    assert calls[-1]["kernel"] is choice


def test_the_header_facts_are_the_kernel_specs_derived_ratio(adapter):
    facts = adapter.kernel_header_facts(unit="saturn_opu", config="any")
    assert facts["ACC_LMUL"][0] == facts["ACC_BITS"][0] // facts["OPERAND_BITS"][0]

"""Package checks: identity, layering, contract schema, plugin references, boards, content hashes.

No hardware is executed and nothing here certifies a compiler candidate.
"""

import hashlib
import json
import subprocess
from pathlib import Path

import pytest
import yaml

from merlin.common.schemas import validate as validate_schema
from merlin.runtime import boards
from merlin.targetgen import provider_compose
from merlin.targetgen.plugins import validate
from merlin.targetgen.providers import ProviderRole, read_provider

ROOT = Path(__file__).resolve().parents[1]


def test_identity_layering_and_plugin_references():
    provider = read_provider(ROOT)
    assert provider.role == ProviderRole.SUPPORT
    assert provider.extends is not None and provider.extends.target == "rvv"
    contract = yaml.safe_load(provider.contract_path.read_text())
    assert contract["name"] == provider.target
    assert validate_schema(contract, "target_contract") == []
    assert validate(contract.get("plugin"), root=ROOT) == []
    assert (ROOT / contract["matrix_contract"]).is_file()


def test_effective_contract_is_fresh_against_the_pinned_base():
    """The committed contract is exactly base @ pinned commit + overlay. Needs the rvv base selected
    (MERLIN_TARGET_PATH) or vendored in the Merlin checkout running this test."""
    fresh, comp = provider_compose.materialize(ROOT, check=True)
    assert fresh, f"stale: run `merlin-target-compose {ROOT}` (base {comp.base.root} @ {comp.base_revision})"
    assert comp.base_revision == read_provider(ROOT).extends.commit


def test_boards_load_and_describe_the_silicon():
    found = boards.load_boards(str(ROOT))
    chip = found["chipyard_kodiak"]
    assert (chip.harts, chip.vector_harts, chip.vlen) == (3, 2, 512)
    assert chip.dram_bytes == 512 << 20
    assert not chip.fpu_sharing and chip.zephyr_vector_ext


def test_existing_opu_board_cannot_be_selected_as_a_zvt_target(monkeypatch):
    """A VME compiler route needs a different RTL configuration and bitstream."""
    matrix = yaml.safe_load((ROOT / "contracts/matrix_units.yaml").read_text())
    opu = matrix["units"]["saturn_opu"]
    assert opu["pin"] == "saturn_opu_int8"
    assert opu["sources"]["crosscheck_header"].endswith("/bme.h")

    overlay = yaml.safe_load((ROOT / "contracts/overlay.yaml").read_text())
    assert overlay["rtl_source"]["config"] == "KodiakOPUConfig"
    assert overlay["runner"]["spike_extension"]["extension_name"] == "saturn_opu"

    board = boards.load_boards(str(ROOT))["firesim_kodiak_opu"]
    assert board.bitstream == "alveo_u250_firesim_kodiak_opu_1core"
    board.check_matrix_route("kodiak", "saturn_opu", "KodiakOPUConfig")
    with pytest.raises(boards.BoardRegistryError, match="does not match"):
        board.check_matrix_route("kodiak", "saturn_vme", "KodiakVMECompact1CoreConfig")
    compact = boards.load_boards(str(ROOT))["firesim_kodiak_vme_compact"]
    compact.check_matrix_route("kodiak", "saturn_vme", "KodiakVMECompact1CoreConfig")
    assert compact.bitstream is None

    from merlin.targetgen.plugins import load_declared

    monkeypatch.setenv("MERLIN_TARGET_PATH", str(ROOT))
    adapter = load_declared("kodiak", "matrix_lowering")

    assert adapter.for_unit("saturn_opu") is adapter
    vme = adapter.for_unit("saturn_vme")
    assert vme.load_contract("saturn_vme").pin == "saturn_vme_kodiak"
    with pytest.raises(KeyError, match="unknown"):
        adapter.for_unit("unknown")


def test_files_match_recorded_content_hashes():
    provenance = json.loads((ROOT / "provenance.json").read_text())
    assert provenance["files"]
    assert not provenance["qualification"]["candidate_certification"]
    assert not provenance["qualification"]["hardware_executed"]
    for record in provenance["files"]:
        assert hashlib.sha256((ROOT / record["path"]).read_bytes()).hexdigest() == record["sha256"], record["path"]


def test_base_submodule_is_the_pinned_base():
    """base/rvv-mlir is the rvv base this layer extends: its gitlink must be extends.commit, so the
    submodule and provider.yaml can never name two different bases."""
    try:
        top = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout")
    if Path(top).resolve() != ROOT.resolve():
        pytest.skip("a copy inside another repository carries no submodule")
    entry = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-s", "base/rvv-mlir"], capture_output=True, text=True, check=True).stdout.split()
    assert entry and entry[0] == "160000", "base/rvv-mlir is not a submodule"
    assert entry[1] == read_provider(ROOT).extends.commit

"""Kodiak's outer-product unit (OPU), end to end: read its facts, emit its kernel, build the routed object.

:mod:`int8_contractions` turns a contraction into ``call @merlin_opu_gemm_i8_<n>``; this builds what that
symbol resolves to. Everything generic about that bridge lives in Merlin:

* the descriptor-ABI bridge, the K-major pack of the left operand, the dense/aligned/fits checks with
  the counted scalar fallback (``merlin_opu_calls`` / ``merlin_opu_fallbacks``), the ordering fences and
  the batched entries: :mod:`merlin.kernels.matrix_shim`;
* the certified microkernel, emitted from the derived encodings: :mod:`merlin.kernels.opu_kernel`;
* the encoding derivation and its cross-check: :mod:`merlin.targetgen.rtl.opu_isa`.

What this module adds is what is true of THIS unit: which sources declare it (``contracts/matrix_units.yaml``),
its geometry from the named configuration, the kernel roles, and two facts the build has to honour.

**Base alignment.** The unit's operand loads move ``dLen`` bits per beat and a multi-beat load from a
misaligned base returns a wrong second beat -- MEASURED, and it presents as a case that passes or fails
depending on what else was linked beside it. So the alignment handed to the shim is ``dLen/8`` of the
named configuration, never a default. What the frozen corpus establishes is narrower than "everything
must be aligned": cases ``m_17``/``m_31``/``n_17``/``n_31`` certify 31/31 at tile edge 32 with row strides
of 17 and 31, i.e. per-row offsets that are misaligned and loads that span two beats. So the requirement
is on the BASE, which is what the shim checks and no more.

**Partial-N padding depth.** The taped-out unit computes an unpadded partial-N tail wrong, and a call
deeper than the kernel's padding scratch falls back to that tail. :func:`build_object` therefore sizes
``KernelSpec.pad_k_max`` to the deepest reduction this build routes.

The shim's pack fence is not decoration on this part either: whole-model spectformer graded cos 0.98288649
with 57 routed calls sharing the pack scratch and no fence between a call's pack and the previous call's
kernel reads, while the identical image was bit-exact on FireSim.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from merlin.kernels import matrix_shim
from merlin.kernels.matrix_shim import (
    DEFAULT_SCRATCH_BYTES,
    ShimUnit,
    is_batched,
    scratch_bytes_for,
)

from ..lower.opu_kernel import KernelSpec, emit_microkernel

# Legacy matrix ABI policy. Shared contraction rewriting requires explicit names.
SIDECAR_NAME = "opu_signatures.json"
SYMBOL_PREFIX = "merlin_opu_gemm_i8"
#: Namespace of the shim's own symbols (pack scratch, counters, the shared body), distinct from the entries.
SHIM_PREFIX = "merlin_opu"

__all__ = [
    "CONTRACT_PATH",
    "DEFAULT_SCRATCH_BYTES",
    "SHIM_PREFIX",
    "SIDECAR_NAME",
    "SYMBOL_PREFIX",
    "OpuBuild",
    "ShimUnit",
    "UnitContract",
    "build_object",
    "derive_encodings",
    "emit_translation_unit",
    "is_batched",
    "load_contract",
    "scratch_bytes_for",
]

#: Where a unit's derivation entry points are declared. Tracked and reviewed, so which sources the
#: compiler reads is a diff someone sees rather than a literal buried in a call.
CONTRACT_PATH = Path(__file__).resolve().parents[3] / "contracts" / "matrix_units.yaml"


def emit_translation_unit(
    encodings: Mapping[str, Any],
    signatures: Mapping[str, Sequence[int]],
    *,
    spec: KernelSpec | None = None,
    alignment_bytes: int,
    derivation_ok: bool = True,
    scratch_bytes: int | None = None,
) -> ShimUnit:
    """The whole C translation unit: the certified microkernel inside Merlin's generic shim.

    ``signatures`` is :attr:`int8_contractions.ContractionRewrite.signatures` -- ``{symbol: (m, n, k)}`` or
    ``{symbol: (b, m, n, k)}``. ``alignment_bytes`` is the derived operand alignment
    (:func:`kernels.hardware.operand_alignment_for_config`); it is required rather than defaulted because
    a guessed alignment produces a shim that is wrong on the second beat of a load and right everywhere
    a test would look.

    The microkernel is emitted by the same :func:`kernels.opu_kernel.emit_microkernel` the certification
    ran, from the same derived encodings, so what a model executes is the certified code and not a
    transcription of it. ``derivation_ok`` is forwarded, so an unresolved encoding refuses here too.
    """
    spec = spec or KernelSpec(
        accumulate="OPMACC", broadcast="OPMVINBCAST", readout="OPMVOUT"
    )
    kernel = emit_microkernel(encodings, spec, derivation_ok=derivation_ok)
    return matrix_shim.emit_translation_unit(
        kernel,
        spec.func_name,
        signatures,
        alignment_bytes=alignment_bytes,
        prefix=SHIM_PREFIX,
        scratch_bytes=scratch_bytes,
    )


# ==================================================================================================
# Building the object: derive the facts, emit, compile.
# ==================================================================================================


@dataclass(frozen=True)
class UnitContract:
    """One matrix unit's derivation entry points, as declared in :data:`CONTRACT_PATH`.

    Every field is an ADDRESS â€” a file to read or a declaration to look for â€” never a value. The values
    are read out of those files at build time and a value that cannot be read fails closed.
    """

    unit: str
    pin: str
    root_env: str
    path: str
    sources: dict[str, str]
    declarations: dict[str, Any]
    configs: dict[str, Any]
    kernel_roles: dict[str, str]

    def checkout(self) -> Path:
        """The directory the unit's sources live in, resolved the way the rest of the repo resolves it.

        ``paths.env`` rather than ``os.environ``: the checkout is declared in the gitignored ``.env``, and
        reading only the process environment made an earlier consumer of this silently skip on a host
        where the hardware was present.
        """
        from merlin.common.paths import env as _env

        root = _env(self.root_env)
        if not root:
            raise FileNotFoundError(
                f"${self.root_env} is unset, so the sources for {self.unit!r} cannot be located and its "
                "instruction encodings cannot be derived; refusing to emit a kernel from guessed words"
            )
        return Path(root) / self.path if self.path else Path(root)

    def source(self, name: str) -> Path:
        rel = self.sources.get(name)
        if rel is None:
            raise KeyError(
                f"{self.unit!r} declares no {name!r} source in {CONTRACT_PATH}"
            )
        return self.checkout() / rel

    def root(self) -> Path:
        """The integrating SoC's root, which is where its own configs live."""
        from merlin.common.paths import env as _env

        got = _env(self.root_env)
        if not got:
            raise FileNotFoundError(
                f"${self.root_env} is unset, so {self.unit!r} cannot be located"
            )
        return Path(got)

    def config_scala(self) -> list[Path]:
        """Every file that may declare a named configuration of this unit.

        Both the unit's own generator and the integrating SoC's repo, because a heterogeneous config -- the
        unit on one tile beside something else -- is declared by the SoC, and those are the configs real
        bitstreams tend to be built from. Missing files are dropped rather than raising: a contract may name
        a config location that a given checkout does not have, and the failure that matters is "the named
        config was not found in any of them", which the geometry derivation already reports.
        """
        out = [self.checkout() / str(p) for p in self.configs.get("config_scala", ())]
        out += [self.root() / str(p) for p in self.configs.get("host_config_scala", ())]
        return [p for p in out if p.is_file()]

    def mixin_scala(self) -> list[Path]:
        return [
            p
            for p in (
                self.checkout() / str(m) for m in self.configs.get("mixin_scala", ())
            )
            if p.is_file()
        ]

    def geometry(self, config: str) -> tuple[int, int]:
        """``(tile_edge, operand_alignment_bytes)`` for a named configuration, derived from its own Scala."""
        from .. import hardware

        cfgs, mixins = self.config_scala(), self.mixin_scala()
        return (
            hardware.tile_edge_for_config(
                config, config_scala=cfgs, mixin_scala=mixins
            ),
            hardware.operand_alignment_for_config(
                config, config_scala=cfgs, mixin_scala=mixins
            ),
        )

    def spec(self, **overrides) -> KernelSpec:
        """The kernel spec for this unit, with the instruction roles taken from the contract."""
        roles = {
            k: self.kernel_roles[k] for k in ("accumulate", "broadcast", "readout")
        }
        return KernelSpec(**roles, **overrides)


def load_contract(unit: str, *, path: str | Path | None = None) -> UnitContract:
    """Read one unit's block out of the contract file."""
    import yaml

    p = Path(path) if path is not None else CONTRACT_PATH
    payload = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    units = payload.get("units") or {}
    if unit not in units:
        raise KeyError(
            f"{p} declares no matrix unit {unit!r}; declared: {sorted(units)}"
        )
    block = units[unit]
    missing = [
        k
        for k in (
            "pin",
            "root_env",
            "sources",
            "declarations",
            "configs",
            "kernel_roles",
        )
        if k not in block
    ]
    if missing:
        raise ValueError(f"{p}: unit {unit!r} is missing {missing}")
    return UnitContract(
        unit=unit,
        pin=str(block["pin"]),
        root_env=str(block["root_env"]),
        path=str(block.get("path", "")),
        sources=dict(block["sources"]),
        declarations=dict(block["declarations"]),
        configs=dict(block["configs"]),
        kernel_roles=dict(block["kernel_roles"]),
    )


def derive_encodings(contract: UnitContract):
    """The unit's instruction encodings, derived from its RTL and cross-checked against its own header.

    Returns the cross-checked :class:`targetgen.rtl.opu_isa.IsaDerivation`. Its ``ok`` is forwarded into
    the emitter, which refuses to emit when the two sources disagree â€” the funct6 values are ChiselEnum
    ORDINALS, i.e. a function of declaration order in an RTL file, so a stale cross-check header and a
    moved slot are indistinguishable from one source alone.
    """
    from ..lower import instructions as opu_isa

    d = contract.declarations
    derived = opu_isa.derive(
        consts=contract.source("consts"),
        instructions=contract.source("instructions"),
        params=contract.source("params"),
        funct6_enum=str(d["funct6_enum"]),
        consts_container=str(d["consts_container"]),
        insn_seq=str(d["insn_seq"]),
        opcode_name=str(d["opcode_name"]),
        form_funct3={str(k): str(v) for k, v in d["form_funct3"].items()},
    )
    return opu_isa.crosscheck(
        derived,
        contract.source("crosscheck_header"),
        pairs={str(k): str(v) for k, v in d["crosscheck_pairs"].items()},
    )


@dataclass(frozen=True)
class OpuBuild:
    """What a build produced, and what it is a result ABOUT.

    ``provenance`` is not optional bookkeeping: the encodings in this object came from one revision of one
    checkout, and an object attributed to the wrong one is worse than no object because it links and runs.
    """

    object_path: Path
    source_path: Path
    signatures: dict[str, tuple[int, int, int]]
    alignment_bytes: int
    scratch_bytes: int
    tile_edge: int | None = None
    scalar_tile: bool = False
    #: Whether the tile loop was compiled with OpenMP, i.e. whether this object can reach more than one
    #: matrix unit. Recorded rather than inferred: on a chip with a unit per core, a serial object uses
    #: exactly one of them and still computes the right answer, so nothing else distinguishes the two.
    parallel_tiles: bool = False
    provenance: dict[str, Any] = field(default_factory=dict)
    gaps: tuple[str, ...] = ()
    #: Which kernel the routed calls run: the generated one, or a hand-written one from kernels/ with the
    #: sha256 of every file compiled (merlin.kernels.matrix_kernels).
    kernel: dict[str, Any] = field(
        default_factory=lambda: dict(GENERATED_KERNEL_RECORD)
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "object": str(self.object_path),
            "source": str(self.source_path),
            "signatures": {k: list(v) for k, v in self.signatures.items()},
            "alignment_bytes": self.alignment_bytes,
            "scratch_bytes": self.scratch_bytes,
            "tile_edge": self.tile_edge,
            "scalar_tile": self.scalar_tile,
            "parallel_tiles": self.parallel_tiles,
            "provenance": self.provenance,
            "gaps": list(self.gaps),
            "kernel": dict(self.kernel),
        }


#: The record of a build that runs the generated kernel (kernels/kernels.yaml registers it as `generated`).
GENERATED_KERNEL_RECORD = {
    "name": "generated",
    "generated": True,
    "emitter": "kodiak-mlir/opu_kernel.py",
}


def kernel_header_facts(*, unit: str, config: str) -> dict[str, tuple[int, str]]:
    """What a hand-written kernel's generated header states besides the encodings, tile edge and alignment.

    Only derived values or the ABI's own: the accumulator group a full tile row of int32 needs at the operand
    vtype (``acc_bits / operand_bits``, the ratio :class:`KernelSpec` derives), and the element widths.
    """
    spec = load_contract(unit).spec()
    return {
        "OPERAND_BITS": (spec.operand_bits, "int8 operands"),
        "ACC_BITS": (spec.acc_bits, "int32 accumulators"),
        "ACC_LMUL": (
            spec.acc_lmul,
            "LMUL of one accumulator row at e32: acc_bits / operand_bits",
        ),
    }


def generated_kernel(
    *, unit: str, config: str, max_k: int | None = None
) -> tuple[str, str]:
    """``(C source, function)`` of the generated kernel exactly as a build emits it, for kernel-test."""
    contract = load_contract(unit)
    derived = derive_encodings(contract)
    spec = contract.spec(pad_k_max=max(int(max_k or 1), KernelSpec.pad_k_max))
    return emit_microkernel(
        derived.encodings, spec, derivation_ok=derived.ok
    ), spec.func_name


def build_object(*args, **kwargs):
    """Compatibility call; the actual selected-object driver lives in lower/opu_object.py."""
    from ..lower.opu_object import build_object as implementation

    return implementation(*args, **kwargs)

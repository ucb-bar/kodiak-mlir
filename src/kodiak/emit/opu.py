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

import subprocess
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
    (:func:`kernels.opu_cert.operand_alignment_for_config`); it is required rather than defaulted because
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

    Every field is an ADDRESS — a file to read or a declaration to look for — never a value. The values
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
        from ..conformance import opu_cert

        cfgs, mixins = self.config_scala(), self.mixin_scala()
        return (
            opu_cert.tile_edge_for_config(
                config, config_scala=cfgs, mixin_scala=mixins
            ),
            opu_cert.operand_alignment_for_config(
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
    the emitter, which refuses to emit when the two sources disagree — the funct6 values are ChiselEnum
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


def build_object(
    signatures: Mapping[str, tuple[int, int, int]],
    work: str | Path,
    *,
    unit: str,
    config: str,
    cc: str | Path,
    cflags: Sequence[str],
    scalar_tile: bool = False,
    parallel_tiles: bool = False,
    scratch_bytes: int | None = None,
    contract_path: str | Path | None = None,
    kernel: Any = None,
) -> OpuBuild:
    """Derive the unit's facts, emit the translation unit, compile it, and record what it came from.

    ``kernel`` (a ``merlin.kernels.matrix_kernels.KernelChoice``) replaces the generated kernel with a
    hand-written one from ``kernels/``: the shim includes a snapshot of its file beside the generated
    header, and the stand-in twin (``scalar_tile``) compiles Merlin's scalar reference under the same name.
    None (the default) is the generated kernel, byte for byte as before.

    ``unit`` names a block in :data:`CONTRACT_PATH` and is REQUIRED rather than defaulted: a default would
    make this module quietly about one particular extension, which is the overfit the repo's cardinal rule
    forbids. The caller knows which hardware it is building for; this does not.

    ``config`` names the elaborated hardware configuration (e.g. an OPU Shuttle config); the tile edge and
    the operand alignment are read out of ITS OWN declaration, so a build for a different configuration
    gets different geometry without any code change.

    ``scalar_tile`` compiles the tile body as the scalar stand-in instead of the unit's instructions. That
    is what lets a whole model be graded on a host or a plain simulator: it validates the routing, the
    pack, the descriptor ABI and the epilogue while proving nothing about the datapath, which is the
    certification's job. The build records which of the two it is, so a report cannot confuse them.

    ``parallel_tiles`` compiles the tile loop with OpenMP, which is the ONLY way an image reaches more
    than one matrix unit on a chip that has one per core: a routed contraction is a single opaque call by
    the time the parallel transform schedule runs, so the schedule cannot split it and the split has to
    live inside the kernel. Off by default, so a single-core image is compiled exactly as certified and
    acquires no runtime dependency. It is recorded on the build because a serial object on a multi-unit
    chip uses one unit and still computes the right answer -- there is no other symptom.
    """
    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    contract = load_contract(unit, path=contract_path)

    # What revision is this a result about? Verified, never enforced by moving someone's checkout.
    from merlin.common import provenance as PROV

    gaps: list[str] = []
    # `reads` is exactly the contract's declared sources plus the config declarations, so an uncommitted
    # edit to one of THOSE is drift while a stray build log elsewhere in the tree is a note. Passing the
    # precise set rather than letting it default to the pin's requires_paths is what makes the answer about
    # this build instead of about the checkout in general.
    # Only the paths inside the PINNED checkout are checked against the pin: `host_config_scala` lives in
    # the integrating SoC's repo, which this pin does not describe and cannot speak for.
    reads = [
        *contract.sources.values(),
        *(str(c) for c in contract.configs.get("config_scala", ())),
        *(str(m) for m in contract.configs.get("mixin_scala", ())),
    ]
    verification = PROV.verify(contract.pin, checkout=contract.checkout(), reads=reads)
    if not verification.ok:
        gaps.append(
            f"pin {contract.pin} drifted: "
            + "; ".join(
                [
                    *verification.drift,
                    *(
                        [f"missing {list(verification.missing_paths)}"]
                        if verification.missing_paths
                        else []
                    ),
                    *(
                        [f"forbidden present {list(verification.forbidden_present)}"]
                        if verification.forbidden_present
                        else []
                    ),
                ]
            )
        )

    derived = derive_encodings(contract)
    if not derived.ok:
        raise ValueError(
            f"the encoding derivation for {unit!r} did not agree with its cross-check source "
            f"({[c for c in derived.crosschecks if not c.get('agrees')]}); refusing to build an object "
            "from an unresolved encoding, because a wrong field emits a neighbouring instruction rather "
            "than failing to assemble"
        )

    tile_edge, alignment = contract.geometry(config)

    # The partial-N padding must cover every routed reduction: a call deeper than its scratch falls back
    # to the unpadded tail, which the taped-out unit computes wrong. Sized from what this build routes.
    deepest_k = max((int(sig[-1]) for sig in signatures.values()), default=1)
    kernel_record = dict(GENERATED_KERNEL_RECORD)
    if kernel is None:
        unit_src = emit_translation_unit(
            derived.encodings,
            signatures,
            spec=contract.spec(pad_k_max=max(deepest_k, KernelSpec.pad_k_max)),
            alignment_bytes=alignment,
            derivation_ok=derived.ok,
            scratch_bytes=scratch_bytes,
        )
    else:
        prepared = kernel.prepare(work, scalar_tile=scalar_tile)
        kernel_record = prepared.record
        unit_src = matrix_shim.emit_translation_unit(
            prepared.source,
            prepared.function,
            signatures,
            alignment_bytes=alignment,
            prefix=SHIM_PREFIX,
            scratch_bytes=scratch_bytes,
        )
    src = work / "merlin_opu_shim.c"
    src.write_text(str(unit_src), encoding="utf-8")

    cmd = [str(cc), *cflags, "-c", str(src), "-o", str(work / "merlin_opu_shim.o")]
    if parallel_tiles:
        # Defines _OPENMP, which is what the emitted pragma is guarded on.
        cmd[1:1] = ["-fopenmp"]
    if scalar_tile:
        # The host/simulator build needs the edge supplied, since there is no unit to ask for it.
        cmd[1:1] = ["-DOPU_SCALAR_TILE", f"-DOPU_TILE_EDGE={tile_edge}"]
    got = subprocess.run(cmd, capture_output=True, text=True, check=False)
    obj = work / "merlin_opu_shim.o"
    if got.returncode != 0 or not obj.is_file():
        raise RuntimeError(
            f"the emitted matrix-unit shim did not compile:\ncmd: {' '.join(cmd)}\n{got.stderr[-3000:]}"
        )

    prov = PROV.record(
        pins={contract.pin: verification},
        sources=[contract.source(k) for k in sorted(contract.sources)],
        artifacts={"shim_object": obj, "shim_source": src},
    )
    # Which kernel the bytes came from: the image's provenance names it, and a hand-written one by sha256.
    prov["matrix_kernel"] = kernel_record
    return OpuBuild(
        object_path=obj,
        source_path=src,
        signatures={k: tuple(int(x) for x in v) for k, v in signatures.items()},
        alignment_bytes=alignment,
        scratch_bytes=unit_src.scratch_bytes,
        tile_edge=tile_edge,
        scalar_tile=scalar_tile,
        parallel_tiles=parallel_tiles,
        provenance=prov,
        gaps=tuple(gaps),
        kernel=kernel_record,
    )

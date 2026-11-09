"""Compile the selected matrix translation unit; preserve its hardware provenance."""

from __future__ import annotations

import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from ..emit.opu import (
    GENERATED_KERNEL_RECORD,
    SHIM_PREFIX,
    KernelSpec,
    OpuBuild,
    derive_encodings,
    emit_translation_unit,
    load_contract,
    matrix_shim,
)


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

"""Derive OPU operand geometry from selected configuration sources."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

# Operand width of this int8 ABI, rather than a target geometry fact.
_OPERAND_SEW_BITS = 8


def logical_tile_edge(
    vlen_bits: int, *, sew_bits: int = _OPERAND_SEW_BITS, lmul: int = 1
) -> int:
    """The unit's logical tile edge: ``VLMAX`` for the element width the kernel runs at.

    This is the RVV definition (``VLEN * LMUL / SEW``) rather than a fact about any one unit, which is
    why it is computed instead of tabulated -- and the image re-derives the same number from the hardware
    at run time, so a wrong vector length here is caught rather than assumed.
    """
    if vlen_bits <= 0 or sew_bits <= 0 or lmul <= 0:
        raise ValueError(
            f"vlen={vlen_bits} sew={sew_bits} lmul={lmul} must all be positive"
        )
    return (vlen_bits * lmul) // sew_bits


def _joined_text(paths: str | Path | Sequence[str | Path]) -> str:
    """The concatenated text of one or several Scala sources.

    Several, because a unit's configurations do not all live in one file: the extension's own generator
    declares the standalone ones, while an integrating SoC declares the heterogeneous ones that put the
    unit on one tile beside something else. Both are legitimate places to look for a named config, and
    which one a given config lives in is not something this can know -- so the caller names every place
    and this searches all of them. Concatenating is sound because a lookup is by CLASS NAME, which is
    unique across the files by Scala's own rules.
    """
    if isinstance(paths, (str, Path)):
        paths = [paths]
    return "\n".join(Path(p).read_text(encoding="utf-8") for p in paths)


def operand_alignment_for_config(
    config: str,
    *,
    config_scala: str | Path | Sequence[str | Path],
    mixin_scala: Sequence[str | Path],
) -> int:
    """Byte alignment the unit's operand panels require, derived from the datapath width.

    The vector load moves ``dLen`` bits per beat, so a panel that does not start on a ``dLen / 8`` byte
    boundary has its beats split across boundaries. MEASURED on this unit's RTL, that is not merely slower:
    the SECOND beat of an operand load comes back wrong, so a contraction whose operand base was 8 bytes
    off a 16-byte boundary produced correct values in columns 0..15 and garbage from column 16 on. Because
    the address depends on whatever else the image contains, the same shape with the same data passed or
    failed depending on which other cases were compiled beside it -- which is what made this look like
    non-determinism rather than an alignment rule.

    This is a constraint on the unit's CALLERS, so a packing pass that produces operand panels has to
    honour it, and the alignment is part of what packing costs.
    """
    cfg_text = _joined_text(config_scala)
    mixin_text = "\n".join(Path(p).read_text(encoding="utf-8") for p in mixin_scala)
    from .lower import instructions as opu_isa

    params = opu_isa.vector_unit_params(cfg_text, config, mixin_text=mixin_text)
    for key in ("dLen", "dlen", "DLen"):
        if key in params:
            return max(1, int(params[key]) // 8)
    raise ValueError(
        f"could not derive a datapath width for {config!r} from {config_scala} (bound: {params}); "
        "refusing to guess an operand alignment, because an under-aligned panel returns wrong data "
        "rather than failing"
    )


def tile_edge_for_config(
    config: str,
    *,
    config_scala: str | Path | Sequence[str | Path],
    mixin_scala: Sequence[str | Path],
    sew_bits: int = _OPERAND_SEW_BITS,
) -> int:
    """The tile edge for a named hardware config, read from that config's own declaration.

    Raises when the vector length cannot be grounded. That is deliberate: a defaulted vector length gives
    a plausible tile edge, a corpus selected against the wrong geometry, and a certification report that
    claims shapes it never ran.
    """
    cfg_text = _joined_text(config_scala)
    mixin_text = "\n".join(Path(p).read_text(encoding="utf-8") for p in mixin_scala)
    from .lower import instructions as opu_isa

    params = opu_isa.vector_unit_params(cfg_text, config, mixin_text=mixin_text)
    for key in ("vLen", "vlen", "VLen"):
        if key in params:
            return logical_tile_edge(int(params[key]), sew_bits=sew_bits)
    raise ValueError(
        f"could not derive a vector length for {config!r} from {config_scala} (bound: {params}); "
        "refusing to guess one, because the tile edge decides which corpus cases are in reach"
    )

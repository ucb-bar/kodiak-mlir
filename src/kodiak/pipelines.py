"""Named Kodiak matrix routes layered over the RVV base pipeline."""

from merlin.compiler.lower.model import int8_contractions

OPU_W8A8 = "kodiak.opu.w8a8"
VME_W8A8 = "kodiak.vme.w8a8"


def rewrite_opu_prepared_file(prepared, work, *, select, tile_edge, symbol_prefix, sidecar_name):
    return int8_contractions.rewrite_prepared_file(
        prepared,
        work,
        select=select,
        tile_edge=tile_edge,
        symbol_prefix=symbol_prefix,
        sidecar_name=sidecar_name,
    )


def load_opu_signatures(work, *, sidecar_name):
    return int8_contractions.load_sidecar(work, sidecar_name)

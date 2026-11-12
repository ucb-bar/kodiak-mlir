"""Kodiak's physical tile-fill policy for OPU placement."""


def selector(tile_edge: int):
    from merlin.compiler.lower.model import int8_contractions

    return int8_contractions.tile_filling_selector(tile_edge)

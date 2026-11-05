"""Explicit-selection adapter to the provider-owned OPU shim.

This adapter does not certify compilation, hardware, or the transitive source closure.
"""

from . import opu_shim

signature_sidecar_name = opu_shim.SIDECAR_NAME
SIDECAR_NAME = opu_shim.SIDECAR_NAME
shim = opu_shim


def load_contract(unit, *, path=None):
    return opu_shim.load_contract(unit, path=path)


def derive_encodings(contract):
    return opu_shim.derive_encodings(contract)


def geometry(*, unit, config):
    return opu_shim.load_contract(unit).geometry(config)


def selector(tile_edge):
    from merlin.llvmlower import int8_contractions

    return int8_contractions.tile_filling_selector(tile_edge)


def rewrite_prepared_file(prepared, work, *, select, tile_edge):
    from merlin.llvmlower import int8_contractions

    return int8_contractions.rewrite_prepared_file(
        prepared, work, select=select, tile_edge=tile_edge,
        symbol_prefix=opu_shim.SYMBOL_PREFIX, sidecar_name=opu_shim.SIDECAR_NAME,
    )


def load_signatures(work):
    from merlin.llvmlower import int8_contractions

    return int8_contractions.load_sidecar(work, opu_shim.SIDECAR_NAME)


def build_object(signatures, work, *, unit, config, cc, cflags, scalar_tile=False, parallel_tiles=False,
                 kernel=None):
    """``kernel``: a hand-written kernel Merlin selected from ``kernels/kernels.yaml``
    (``merlin.kernels.matrix_kernels.KernelChoice``); None builds the generated kernel exactly as before."""
    extra = {} if kernel is None else {"kernel": kernel}
    return opu_shim.build_object(
        signatures, work, unit=unit, config=config, cc=cc, cflags=cflags,
        scalar_tile=scalar_tile, parallel_tiles=parallel_tiles, **extra,
    )


def kernel_header_facts(*, unit, config):
    """Configuration facts the generated header of a hand-written kernel carries."""
    return opu_shim.kernel_header_facts(unit=unit, config=config)


def generated_kernel(*, unit, config, max_k=None):
    """``(C source, function)`` of the generated kernel, for Merlin's kernel-test."""
    return opu_shim.generated_kernel(unit=unit, config=config, max_k=max_k)

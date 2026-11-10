"""Explicit-selection adapter to the provider-owned OPU shim.

This adapter does not certify compilation, hardware, or the transitive source closure.
"""

from .. import pipelines, placement
from ..emit import opu as opu_shim

API_VERSION = 1
generated_output_semantics = "overwrite"


def for_unit(unit):
    """Bind each instruction set to its own immutable adapter."""
    if unit == "saturn_opu":
        import sys

        return sys.modules[__name__]
    if unit == "saturn_vme":
        from pathlib import Path

        from ..emit.vme import VmeMatrixAdapter

        return VmeMatrixAdapter(
            Path(__file__).resolve().parents[3] / "contracts" / "matrix_units.yaml",
            unit,
        )
    raise KeyError(unit)


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
    return placement.selector(tile_edge)


def rewrite_prepared_file(prepared, work, *, select, tile_edge):
    return pipelines.rewrite_opu_prepared_file(
        prepared,
        work,
        select=select,
        tile_edge=tile_edge,
        symbol_prefix=opu_shim.SYMBOL_PREFIX,
        sidecar_name=opu_shim.SIDECAR_NAME,
    )


def load_signatures(work):
    return pipelines.load_opu_signatures(work, sidecar_name=opu_shim.SIDECAR_NAME)


def certification_cases(tile_edge, *, corpus_path):
    """Shapes from an explicitly selected corpus; deferrals cannot count as coverage."""
    from ..conformance import opu_corpus

    runnable, deferred = opu_corpus.select(tile_edge, path=corpus_path)
    if deferred:
        raise ValueError(
            "selected corpus contains deferred cases; complete coverage is not established"
        )
    return runnable


def build_object(
    signatures,
    work,
    *,
    unit,
    config,
    cc,
    cflags,
    scalar_tile=False,
    parallel_tiles=False,
    kernel=None,
):
    """``kernel``: a hand-written kernel Merlin selected from ``kernels/kernels.yaml``
    (``merlin.kernels.matrix_kernels.KernelChoice``); None builds the generated kernel exactly as before."""
    extra = {} if kernel is None else {"kernel": kernel}
    return opu_shim.build_object(
        signatures,
        work,
        unit=unit,
        config=config,
        cc=cc,
        cflags=cflags,
        scalar_tile=scalar_tile,
        parallel_tiles=parallel_tiles,
        **extra,
    )


def kernel_header_facts(*, unit, config):
    """Configuration facts the generated header of a hand-written kernel carries."""
    return opu_shim.kernel_header_facts(unit=unit, config=config)


def generated_kernel(*, unit, config, max_k=None):
    """``(C source, function)`` of the generated kernel, for Merlin's kernel-test."""
    return opu_shim.generated_kernel(unit=unit, config=config, max_k=max_k)

"""Compatibility entrypoint; Kodiak implementation lives in ``src.kodiak.lower.interface``."""

from .src.kodiak.lower import interface as _implementation


def __getattr__(name: str):
    if name.startswith("__"):
        raise AttributeError(name)
    return getattr(_implementation, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_implementation)))


def for_unit(unit):
    """Preserve the selected plugin module's identity for the OPU route."""
    if unit == "saturn_opu":
        import sys

        return sys.modules[__name__]
    return _implementation.for_unit(unit)

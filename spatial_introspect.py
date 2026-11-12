"""Compatibility entrypoint; Kodiak implementation lives in ``src.kodiak.rtl.spatial``."""

from .src.kodiak.rtl import spatial as _implementation


def __getattr__(name: str):
    if name.startswith("__"):
        raise AttributeError(name)
    return getattr(_implementation, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_implementation)))

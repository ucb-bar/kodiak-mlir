"""Compatibility entrypoint; Kodiak implementation lives in ``src.kodiak.conformance.opu_cert``."""

from .src.kodiak.conformance import opu_cert as _implementation


def __getattr__(name: str):
    if name.startswith("__"):
        raise AttributeError(name)
    return getattr(_implementation, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_implementation)))

"""Validate Kodiak's explicit component composition at the plugin boundary."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def validate() -> None:
    """Reject stale or ambiguous RVV/Kodiak composition before selecting a route."""
    from merlin.compiler.backend.composition import validate_backend_composition

    validate_backend_composition(ROOT, bases=[ROOT / "base/rvv-mlir"])

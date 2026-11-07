"""Kodiak's versioned accelerator extension over the inherited RVV backend."""

from merlin.llvmlower.feature_registry import ImprFeature
from merlin.targetgen.extension_offers import MatrixExtension

API_VERSION = 1


def bind(routing):
    return MatrixExtension(routing)


def feature_definitions() -> tuple[ImprFeature, ...]:
    """Names accepted by Kodiak builds in addition to the inherited RVV features."""
    return (
        ImprFeature(
            name="opu_matmul",
            action_class="PASS",
            description=(
                "Route eligible int8 contractions to Kodiak's selected OPU kernel. "
                "Routing runs before the RVV schedule and does not edit that schedule."
            ),
        ),
    )

"""Kodiak's versioned accelerator extension over the inherited RVV backend."""

from merlin.llvmlower.feature_registry import ImprFeature
from merlin.targetgen.extension_offers import MatrixExtension

API_VERSION = 1
OPU_ROUTE_FEATURE = "opu_matmul"


class KodiakMatrixExtension(MatrixExtension):
    def route_feature_name(self) -> str:
        """Name the chip-owned feature that enables OPU contraction routing."""
        return OPU_ROUTE_FEATURE


def bind(routing):
    return KodiakMatrixExtension(routing)


def feature_definitions() -> tuple[ImprFeature, ...]:
    """Names accepted by Kodiak builds in addition to the inherited RVV features."""
    return (
        ImprFeature(
            name=OPU_ROUTE_FEATURE,
            action_class="PASS",
            description=(
                "Route int8 rank-2 contractions with a zero accumulator init to the certified outer-product "
                "matrix microkernel, as calls to a generated translation unit that transposes the left operand "
                "K-major and reads its extents from the memref descriptors. NOT a schedule or pipeline edit: "
                "the rewrite happens on the prepared IR (llvmlower/int8_contractions), which is why both hooks are "
                "None. Selection is a separate decision -- which contractions move is answered by the cost "
                "model / e-graph and passed in, so enabling this feature without a selector routes nothing. "
                "Coverage on spectformer int8: 90 of 106 contractions are legal (the 16 batch_matmuls are "
                "gapped by a matmul-only contract), and a tile-filling selector at edge 32 moves 41 of them, "
                "which is the shapes carrying ~88% of the arithmetic. Requires a pinned implementation "
                "revision carrying the unit at build time, because the instruction encodings are derived "
                "from its RTL rather than written down. Default-off."
            ),
        ),
    )

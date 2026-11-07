"""Build a convolution through Merlin's selected OPU kernel and check the route.

Run from any directory with --merlin /path/to/merlin, or recheck a saved
summary without building with --summary /path/to/summary.json. This is a
functional Spike example, not a physical Kodiak or TorchAO source-model gate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1]
KERNEL = PACKAGE / "kernels" / "opu_tile_loop.c"
KERNEL_NAME = "opu_tile_loop"


def check(summary_path: Path) -> dict[str, object]:
    """Refuse a successful-looking run that did not use the selected kernel."""
    summary_path = summary_path.resolve(strict=True)
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    report = json.loads(
        (summary_path.parent / "routing_report.json").read_text(encoding="utf-8")
    )
    matrix = summary["matrix"]
    on_spike = summary["matrix_on_spike"]
    route = matrix["matrix"]
    expected_sha = hashlib.sha256(KERNEL.read_bytes()).hexdigest()
    selected = report.get("kernel") or {}
    built = route.get("kernel") or {}

    if matrix.get("status") != "verified" or not (matrix.get("verify") or {}).get(
        "gate_ok"
    ):
        raise ValueError("matrix image did not pass its W8A8 gate")
    if (
        on_spike.get("status") != "verified"
        or on_spike.get("bit_exact_vs_twin") is not True
    ):
        raise ValueError("shipped OPU image did not match the scalar twin on Spike")
    if report.get("unit") != "saturn_opu" or report.get("config") != "KodiakOPUConfig":
        raise ValueError("routing report names a different unit or configuration")
    if report.get("routed_contractions", 0) < 1 or report.get("macs_routed", 0) < 1:
        raise ValueError("convolution routed no integer MACs to the OPU")
    if (report.get("macs_by_site") or {}).get("conv", {}).get("routed", 0) < 1:
        raise ValueError("routing report has no convolution MACs on the OPU")
    for label, kernel in (("routing report", selected), ("build", built)):
        if kernel.get("name") != KERNEL_NAME or kernel.get("sha256") != expected_sha:
            raise ValueError(f"{label} did not bind the current {KERNEL_NAME} C bytes")
    for name in ("matrix.elf", "matrix_twin.elf"):
        path = summary_path.parent / name
        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError(f"missing saved image: {path}")

    return {
        "kernel": KERNEL_NAME,
        "kernel_sha256": expected_sha,
        "routed_contractions": report["routed_contractions"],
        "conv_macs_routed": report["macs_by_site"]["conv"]["routed"],
        "shipped_matches_scalar_twin_on_spike": True,
        "routing_report": str(summary_path.parent / "routing_report.json"),
    }


def build(merlin: Path) -> Path:
    merlin = merlin.resolve(strict=True)
    launcher = merlin / "examples" / "kodiak" / "run.sh"
    if not launcher.is_file():
        raise ValueError(f"not a Merlin checkout: {merlin}")
    env = os.environ.copy()
    env["MERLIN_TARGET_PATH"] = str(PACKAGE)
    env["MERLIN_BOARD_CATALOG"] = (
        f"{merlin / 'examples' / 'board-catalog.yaml'}:{PACKAGE}"
    )
    cmd = [
        str(launcher),
        "op",
        "conv2d",
        "16",
        "64",
        "16",
        "3",
        "--harts",
        "1",
        "--matrix-kernel",
        KERNEL_NAME,
    ]
    print("$ " + " ".join(cmd), flush=True)
    process = subprocess.Popen(
        cmd,
        cwd=merlin,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    kept = None
    assert process.stdout is not None
    for line in process.stdout:
        print(line, end="", flush=True)
        if "  kept in: " in line:
            kept = Path(line.split("  kept in: ", 1)[1].strip())
    if process.wait() != 0:
        raise RuntimeError(f"Merlin operator gate exited {process.returncode}")
    if kept is None:
        raise RuntimeError("Merlin did not report its saved operator artifacts")
    return (kept if kept.is_absolute() else merlin / kept) / "summary.json"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--merlin",
        type=Path,
        help="Merlin checkout in which to build and run the operator",
    )
    parser.add_argument(
        "--summary", type=Path, help="verify an existing saved run without building"
    )
    args = parser.parse_args()
    if (args.merlin is None) == (args.summary is None):
        parser.error("choose exactly one of --merlin or --summary")
    result = check(args.summary if args.summary is not None else build(args.merlin))
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

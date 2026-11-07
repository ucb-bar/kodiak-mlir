# Follow a convolution to a Kodiak OPU kernel

First complete the [Kodiak setup](../README.md) in Merlin, select the declared
`saturn_opu_int8` Chipyard source with `MERLIN_CHIPYARD`, and build/export the
Kodiak OPU Spike extension with `examples/kodiak/run.sh spike-model`. This
example requires the shipped image to run on that functional model; it refuses
a skipped simulator leg.

From a configured Merlin checkout, run:

```sh
python /path/to/kodiak-mlir/examples/conv2d_to_opu.py --merlin "$PWD"
```

The script selects this package through `MERLIN_TARGET_PATH`, then runs Merlin's
`examples/kodiak/run.sh op conv2d 16 64 16 3 --harts 1 --matrix-kernel opu_tile_loop`.
Merlin captures the convolution, lowers its signed-int8 contraction to the common
GEMM call, chooses the OPU route, and compiles `kernels/opu_tile_loop.c` into the
shipped image. The scalar twin uses Merlin's independent reference kernel.

The script checks the saved `summary.json` and `routing_report.json`: at least one
convolution contraction and MAC routed, the selected kernel's SHA-256 equals the
current C file, the matrix image passed its W8A8 gate, and the shipped image
matched its scalar twin bit for bit on the OPU Spike model. It prints the report
path and SHA. To recheck a copied run without building:

```sh
python /path/to/kodiak-mlir/examples/conv2d_to_opu.py --summary /path/to/summary.json
```

This exercises Merlin's int8 operator capture and functional OPU model. It is
not an exact TorchAO source-model parity result or a physical-chip test. The
hand-written tile loop does not implement the taped-out OPU's partial-N padding
workaround; use the generated kernel for physical images with partial-N tiles.
See the package [kernel guide](../README.md#write-your-own-kernel) for the ABI,
registry entry, and `kernel-test` shape sweep.

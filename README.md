# kodiak-mlir

The refactor consumes Merlin's public `compiler.backend.composition` and
`compiler.backend.proposal` interfaces. Composition validates the exact RVV
submodule in `base/rvv-mlir`; initialize it with `git submodule update --init`.
`backend.toml` declares Kodiak's added routes. OPU/VME emitters remain here, and
the root plugin entrypoints retain their existing call signatures. Board APIs
receive the explicit `boards.yaml` path. These contracts do not certify execution.

Merlin support for the **Kodiak** tapeout: Saturn vector units plus the int8 **outer-product unit
(OPU)**. This repository is a layer on top of [`rvv-mlir`](https://github.com/ucb-bar/rvv-mlir). It
inherits all of RVV and adds only what Kodiak has:

| what | where |
|---|---|
| the delta to the rvv contract (OPU compute unit, RTL config, measurement) | `contracts/overlay.yaml` |
| the effective contract Merlin reads (generated, do not edit) | `contracts/target_contract.yaml`, `contracts/composed.lock.yaml` |
| Kodiak RVV tuning choices and their chip evidence | [`schedules/kodiak_prepack_w8a8_v1/`](schedules/kodiak_prepack_w8a8_v1/README.md) |
| where the OPU's facts are read from (Chisel sources, config files, kernel roles) | `contracts/matrix_units.yaml` |
| the OPU kernel (partial-N padding to the deepest routed K, OpenMP tile split, scalar twin), built inside Merlin's generic `matrix_shim` | `src/kodiak/emit/opu.py` |
| the whole-model hook: which contractions go to the OPU, and building the object | `src/kodiak/lower/interface.py` |
| hand-written OPU kernels, their registry and constraints ([Write your own kernel](#write-your-own-kernel)) | `kernels/` |
| the boards: Kodiak silicon, the 2-core OPU elaboration, FireSim 1-core | `boards.yaml` |
| how to run on the chip (shared rig: check it is idle first) | [`RUNNING.md`](RUNNING.md) |
| how to build the Kodiak+OPU FireSim bitstream (config patches, FPGA clock-gate model) | [`firesim/`](firesim/README.md) |

```
rvv-mlir   (RVV contract, executable codegen, generic schedules)
 └── kodiak-mlir   (+ OPU unit, + Kodiak boards)    <- this repo
```

`provider.yaml` pins the exact rvv-mlir commit this layer was composed against. CI runs
`merlin-target-compose --check .`, so a change to the base shows up as a stale contract here and is
never inherited silently.

Kodiak's executable code lives under `src/kodiak/`: `backend.py` exposes the matrix extension,
`composition.py` validates its declared RVV component and chip ownership, `placement.py` selects
tile candidates, `pipelines.py` names Kodiak's OPU and VME routes, `lower/` derives instruction
facts and implements the adapter, `emit/` builds the OPU/VME objects, and `conformance/` holds the
OPU corpus. `backend.toml` declares added operations and plugin owners; `provider.yaml` remains
the authoritative identity and RVV revision. Root Python files are compatibility entrypoints for
Merlin's existing plugin and Spike loaders. RVV codegen stays in `rvv-mlir`; generic scalar and LLVM
finalization stay in Merlin.
The manifest marks OPU and compact VME as added **unit routes**: the inherited RVV route remains
available, and placement chooses a chip route only for a legal selected configuration. There is no
invented RVV operation name to override. A future replacement must name its original pipeline and
preserved contract explicitly; configuration restrictions belong in the chip contract and board gate.

## Compile for Kodiak

Use a Merlin checkout. Merlin fetches this package at its pinned commit during setup and selects it
through the generated board catalog:

```sh
git clone --branch feat/OPU-VME https://github.com/ucb-bar/merlin.git && cd merlin/examples/kodiak
./run.sh setup        # Merlin venv + model capture env + .env; prints the commands for anything missing
                      # (LLVM/MLIR 23, chipyard with the OPU, Zephyr workspace + SDK)
```

**Test one operator** -- the quickest loop for an OPU kernel:

```sh
./run.sh op gemm                  # 64x64x64: RVV vs RVV+OPU, both gated on spike
./run.sh op gemm 96 80 200        # M N K (partial tiles, odd K)
./run.sh op conv2d 16 64 16 3     # CIN COUT HW K
./run.sh op path/to/loader.py     # your own one-op model
```

**A whole model** -- anything model2MLIR can capture:

```sh
./run.sh capture my_model --loader path/to/my_model/loader.py   # get_model_and_inputs()
./run.sh build --opu --models my_model                          # RVV and RVV+OPU, 1 and 2 harts
./run.sh package --opu --models my_model                        # zip for the chip (see RUNNING.md)
```

One model at one hart count, without packaging:

```sh
merlin-compile --workload <model> --dtype int8 --target rvv --board chipyard_kodiak --harts 2 \
  --matrix-unit saturn_opu --matrix-config KodiakOPUConfig --matrix-support-target kodiak \
  --run spike --verify
```

Stock spike does not execute the OPU, so the gate runs the image's **stand-in twin** (with the spike
model below, `op` also runs the shipped image): the same lowering
(checked by digest) with the OPU tile replaced by a scalar loop. That checks the routing, the K-major
pack, the call ABI and the requant epilogue; the OPU datapath itself is certified on its RTL
(`merlin.kernels.opu_cert`). The shipped ELF is audited to contain the OPU's instructions, and the twin
to contain none.

Every build writes `routing_report.json`, which lists which contractions went to the OPU and why the
others did not. Merlin supports transposed and strided operand layouts by packing them for the OPU.
The routing decision compares a geometry-derived OPU cost with the RVV cost, including tile padding;
small matmuls can route when the OPU is still the cheaper choice. Read the report before expecting a
speedup.

### The OPU on spike: `spike/`

`spike/` holds a functional model of the OPU as a spike `--extlib` extension, so the shipped image (with
the OPU's instructions) can also run on spike:

| file | what |
|---|---|
| `spike/opu_ext.cc` | the model. It walks the RTL's sequencer iterations over the array's physical cell registers (OuterProductSequencer, Backend, OuterProductUnit). It contains no encoding and no geometry. |
| `spike/build.py` | derives the instruction words (`opu_isa`, cross-checked against `bme.h`), the config's vLen/dLen and the `OPUParameters` defaults. It writes `opu_encodings.h`, compiles against the headers of the spike Merlin runs, and writes `manifest.json` (pin verification, source digests, .so sha256) |
| `spike/model.yaml` | where those extra facts are read from, plus the extension name |

```sh
python spike/build.py --out <merlin>/out/build/spike-ext/kodiak    # or: examples/kodiak/run.sh spike-model
```

Merlin loads it only as `contracts/overlay.yaml` declares it (`runner.spike_extension`: name,
`extlib_env: MERLIN_KODIAK_OPU_SPIKE_EXTLIB`, `built_from`). The declaration pins the model's INPUTS --
the source, the RTL-derived encodings header and `model.yaml` -- so a build with your own compiler and
spike is accepted when its `manifest.json` describes that .so, those inputs and a passing self-test.
Changing the model or the RTL it reads changes those digests: re-declare them and recompose
(`merlin-target-compose .`).

Model results (2026-11-04): Merlin's OPU certification corpus 52/52 on spike with the model; the shipped
images of `op gemm 64 64 64`, `op gemm 96 80 200` and `op conv2d 16 64 16 3` match their stand-in twins
bit for bit.

What the model covers:

* `vl == 0` drops the instruction, as Dispatch does. Otherwise vl is not read, which matches the RTL.
* `vstart != 0` and `vill` trap.
* `rd[4]` is the column flag for every instruction.
* An iteration count the sequencer's `col_idx` cannot reach (for example a readout at e32/m1) hangs the
  RTL. The model raises illegal-instruction instead and says so on stderr.

What it does not cover: timing, including the read-intent hazard that `row_vreg_alt` works around, and
the taped-out part's partial-N erratum, which belongs to another revision.

## Write your own kernel

The OPU kernel every routed int8 contraction calls can be a plain C file in `kernels/`, picked with one
flag. Merlin builds the model with it, builds the stand-in twin from its own scalar reference, and requires
the shipped image (your kernel, on spike's OPU model) to match the twin bit for bit.

**The ABI.** One function, exactly this signature (`OPU_KERNEL(my_gemm)` spells it):

```c
void my_gemm(int32_t *c, const int8_t *at, const int8_t *b, const int32_t *bias, size_t m, size_t n, size_t k);
/* C[i][j] = (bias ? bias[j] : 0) + sum_k at[k*m + i] * b[k*n + j]     int32, wrapping; C is WRITTEN */
```

* `at`: A, **K-major** (`k` rows of `m` int8, row stride `m`). Merlin packs (transposes) the model's A into
  an aligned scratch buffer for you.
* `b`: B, **K-major** (`k` rows of `n`, row stride `n`): the weights, in place.
* `c`: int32, row-major `m x n` (row stride `n`). Write every element; do not accumulate into it.
* `bias`: `n` int32 or NULL. The model always passes NULL (the bias is folded elsewhere); kernel-test also
  checks a non-NULL one unless you declare `bias: false`.
* `m, n, k >= 1`, any values unless you declare constraints. `at`, `b`, `c` are aligned to
  `OPU_ALIGN_BYTES`. Requantization to int8 happens after you return (Merlin's epilogue): stop at int32.
* On a multi-hart image the file is compiled with `-fopenmp`; split your tile loop with
  `#pragma omp parallel for` to use every hart's OPU (see `opu_tile_loop.c`).

**The header.** `#include "saturn_opu.h"`. Merlin writes it at build time from the encodings it derives
from the OPU's RTL (cross-checked against `bme.h`), so you name instructions and never type a word:

| macro | |
|---|---|
| `OPU_OPMACC(rd, rs1, rs2)`, `OPU_OPMVINBCAST(...)`, `OPU_OPMVOUT(...)`, `OPU_OPMVIN(...)` | the `.insn r` directive as a string, for an `asm volatile`; operands are strings: `OPU_REG(1)` or `"%[r]"` |
| `OPU_<NAME>_R(rd, rs1, rs2)` | the same as a whole asm statement, operands as register numbers |
| `OPU_REG(n)`, `OPU_STR(x)` | operand spelling (`.insn` takes x-register names; the field encodes the number) |
| `OPU_TILE_EDGE`, `OPU_ALIGN_BYTES`, `OPU_ACC_LMUL`, `OPU_ACC_BITS`, `OPU_OPERAND_BITS` | this configuration's facts |
| `OPU_KERNEL(name)` | the ABI |

`run.sh kernel-test` writes the header beside its report
(`out/artifacts/probes/kernel-test/kodiak/<kernel>/saturn_opu.h`) so you can read it first.

**The template.** `kernels/opu_tile_loop.c`: bias broadcast into the accumulator, one `OPMACC` per
reduction row (each step one asm statement: zero + load A, load B, accumulate; A alternates v5/v6 because of
the read-intent hazard in the unit's sequencer), then a row-by-row `OPMVOUT` readout. Copy it.

**The registry.** `kernels/kernels.yaml` lists each kernel (`unit`, `file`, `function`, `description`,
`constraints`) and the default (`generated`: the kernel `opu_shim.py` emits, unchanged). Constraints are
optional: `m_multiple`, `n_multiple`, `k_multiple`, `max_m`, `max_n`, `max_k` (integers or `tile`,
`2*tile`...) and `bias: false`. Merlin refuses a build that would give the kernel any other shape, naming
the contraction and the rule.

**The loop** (from a Merlin checkout; your clone of this repo on `MERLIN_TARGET_PATH`, so an edit is picked
up by the next command and no pin moves):

```sh
export MERLIN_TARGET_PATH=$HOME/src/kodiak-mlir
cp $HOME/src/kodiak-mlir/kernels/opu_tile_loop.c $HOME/src/kodiak-mlir/kernels/my_gemm.c   # + a kernels.yaml entry
examples/kodiak/run.sh kernel-test my_gemm                                # 1. seconds, ~90 shapes, a table
examples/kodiak/run.sh op gemm 96 80 200 --matrix-kernel my_gemm --harts 2   # 2. one operator, end to end
examples/kodiak/run.sh matrix --models spectformer --matrix-kernel my_gemm   # 3. whole models (or package --opu)
```

`kernel-test` runs your kernel on spike's OPU model over partial M/N around one and two tiles, K tails,
deep K, multi-tile shapes, a bias, full-range operands, back-to-back calls and the OPU certification
corpus's shapes, and prints `C[i][j]: got X, reference Y` for the first wrong element, elements never
written, and writes past the end of C. `--matrix-kernel path/to/k.c[:function]` tries a file outside this
directory. Merlin's side is documented in its `docs/guides/chip_packages.md` ("Write your own kernel").

## Add a kernel or an instruction

* **A hand-written OPU kernel**: see "Write your own kernel" above.
* **The generated OPU microkernel** (blocking, a fused epilogue): the C is emitted by
  `src/kodiak/emit/opu.py` from `src/kodiak/lower/opu_kernel.py` and wrapped in Merlin's generic
  `merlin.compiler.lower.matrix` (descriptor ABI, K-major pack, counted scalar fallback, batched
  entries). Its instruction words are derived from the RTL, never written down.
  `src/kodiak/conformance/opu_cert.py` certifies a kernel against the scalar reference corpus.
* **A new matrix instruction or unit**: add a `units:` block to `contracts/matrix_units.yaml` naming
  the Chisel sources, the declarations the derivation reads and the kernel roles; pin the RTL revision
  in Merlin's `hardware_pins.yaml`; give `src/kodiak/lower/interface.py` a `build_object` for it.
* **Which contractions go to the unit**: `src/kodiak/placement.py` (default: both `M` and `N` at least
  one tile edge). Profitability belongs in Merlin's cost model, not in a per-model rule.

### Zvt on a future Kodiak configuration

The shipped Kodiak tapeout and `firesim_kodiak_opu` bitstream implement the four BME instructions from
Saturn `opu-int8`. They do **not** decode the Zvt/VME words. `saturn_vme` is therefore not a valid
`--matrix-unit` choice for `KodiakOPUConfig`; compiling Zvt words for that configuration
would produce illegal instructions. Iris's separately pinned `IrisConfig` and Zvt Spike path live in
`iris-mlir` and do not establish Kodiak hardware support.

To add a Kodiak VME target, first land a Chipyard configuration that instantiates Saturn `vme` with
Rocket's matrix configuration decoder, and pin those RTL revisions. Then add a **new** matrix contract,
board identity and lowering route for that configuration; derive and cross-check its encodings against
the RTL, `vme.h` and pinned Zvt Spike. Gate the shipped image on Zvt Spike, run the same image on RTL,
and only advertise FireSim after a matching bitstream has been built and verified. Keep
`firesim_kodiak_opu` bound to its existing BME contract throughout.

## Status

* RVV images: h1 and h2 pass on the chip (2026-08). h3 (scalar third hart) is unexplained on silicon.
* OPU, h1 on the chip (2026-08-19): spectformer int8 bit-exact, 1.055× whole-model over RVV on the rig
  (OPU region 1.71×); FireSim 1-core 1.58×. Partial-N tiles need padding (on by default).
* OPU, h2: built; no chip result yet.
* Merlin flow (2026-11-04, spike stand-in twin, W8A8 golden): single GEMMs (64^3, 96x80x200, 64x200x72,
  128x128x64 at 2 harts), a conv2d and a 2-layer encoder all route to the OPU and pass.

## Checks

```sh
python -m pytest tests     # with Merlin installed; the rvv base must be selected or vendored
```

## The rvv base (`base/rvv-mlir`)

This package is a layer on [rvv-mlir](https://github.com/ucb-bar/rvv-mlir): `contracts/overlay.yaml` holds only what
this chip adds or changes, and `contracts/target_contract.yaml` is base + overlay, generated. The base is a submodule
at `base/rvv-mlir`, pinned to the same commit as `extends.commit` in `provider.yaml` (a test keeps the two equal).

```sh
git clone --recurse-submodules -b handwritten-implementation https://github.com/ucb-bar/kodiak-mlir.git
MERLIN_TARGET_PATH=base/rvv-mlir merlin-target-compose . --check     # is the generated contract fresh?
```

Changes that belong to every RVV chip go to rvv-mlir itself (commit and push there). Changes for this chip only go
in `contracts/overlay.yaml` (use `replace:` / `remove:` to override a base fact). To move to a newer base:
`git -C base/rvv-mlir checkout <sha>`, set `extends.commit` to the same sha, run `merlin-target-compose .`, commit.


## Installed prepared contraction service

`pip install .` installs the `merlin.backends:kodiak` provider. This bounded
route uses the same selector, contraction rewrite and matrix object builder as
`matrix_lowering.py`; it does not require or copy the RVV submodule. The existing
whole-model plugin composition still validates its explicitly pinned RVV base.
Open `service.py` for chip contract selection and the existing target callbacks.
Merlin `compiler/lower/prepared.py` owns shared option validation, rewrite/object
orchestration and ordinary compilation records; chip implementations remain here.

```python
from merlin.api import compile
from merlin.compiler.request import Source, CompileOptions

result = compile(Source(prepared_text, kind="upstream-mlir"),
    backend="kodiak", output="out/matrix", options=CompileOptions(parameters={
        "kodiak.unit": "saturn_opu",
        "kodiak.config": "KodiakOPUConfig",  # select your actual RTL configuration
        "kodiak.emit": "object",
        "kodiak.cc": "/absolute/path/to/riscv64-unknown-elf-gcc",
        "kodiak.cflags": ["-O2", "-march=<selected-ISA>", "-mabi=<selected-ABI>"],
    }))
```

The selected contract requires its external chip/RTL sources and hardware pin
inventory, including configuration declarations. Geometry and instruction words
are derived and cross-checked; they are never supplied as guessed constants.
`kodiak.emit=prepared-ir` omits compiler options and returns rewritten partial IR,
signatures and a receipt. Object emission additionally needs an explicit C
compiler and target flags. Neither mode requires LLVM emission. Both refuse
inputs with no admitted contractions. No model capture or quantization occurs.

The partial IR retains unrouted operations and calls to the matrix shim. The
object contains that shim and target kernel; CPU/RVV lowering, ABI admission,
linking and the selected platform remain separate obligations. These products
are not executable images. No platform, hart scheduling, simulator, hardware
execution or numerical qualification is established by this service. Existing
handwritten delivery and verification entrypoints remain available.

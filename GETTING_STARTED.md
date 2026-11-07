# Start compiling for Kodiak on a new machine

This guide produces a Zephyr ELF and shows how to replace its OPU kernel. It uses
Merlin for capture, routing and final compilation, `rvv-mlir` for the inherited
vector lowering, and this package for Kodiak boards and OPU instructions. The
source model or its saved capture is an input; neither model weights nor a
FireSim bitstream is stored in these Git repositories.

## 1. Set up the compiler

Install `git`, `uv`, `make`, `g++`, and `dtc`. You also need an LLVM/MLIR 23
install, a Chipyard checkout with RISC-V tools and the pinned Kodiak OPU Saturn
source, and Zephyr SDK 0.17.0. Merlin's
[LLVM guide](https://github.com/ucb-bar/merlin/blob/feat/OPU-VME/docs/guides/llvm_toolchain.md)
explains the LLVM build. `setup` checks the hardware source files against their
declared pins; its missing-item report gives the exact commands for prerequisites
it cannot install. Do not assume a branch name proves an RTL pin.

```sh
mkdir -p "$HOME/kodiak-work"
cd "$HOME/kodiak-work"
git clone --branch feat/OPU-VME https://github.com/ucb-bar/merlin.git
git clone --branch handwritten-implementation --recurse-submodules \
  https://github.com/ucb-bar/kodiak-mlir.git
cd merlin/examples/kodiak
./run.sh setup --llvm /absolute/path/to/llvm-install \
  --chipyard /absolute/path/to/chipyard \
  --zephyr-sdk /absolute/path/to/zephyr-sdk-0.17.0
./run.sh preflight --require package --require-pin saturn_opu_int8
```

`setup` creates Merlin's Python environment, fetches **the exact Kodiak and
model2MLIR revisions Merlin pins**, prepares the capture environment and Zephyr
workspace, and builds the OPU functional model for Spike. It records selected
paths in `merlin/.env`. It is safe to rerun after installing a missing item.
`preflight` must pass before compiling. If the RTL source differs, resolve the
pin reported by setup; an off-pin build records the drift and is a different
qualification.

The editable Kodiak clone above includes `base/rvv-mlir` at the commit in
`provider.yaml`. To make Merlin read **your live kernel edits** instead of the
immutable fetched Kodiak package, export this in the shell running `run.sh`:

```sh
export MERLIN_TARGET_PATH="$HOME/kodiak-work/kodiak-mlir:$HOME/kodiak-work/kodiak-mlir/base/rvv-mlir"
```

The first directory owns Kodiak's OPU; the second owns RVV lowering. Merlin's
default fetched package is preferable when reproducing a published result.
For frontend development, model2MLIR is a separate project; `setup` fetches
the **capture-pinned revision** into `merlin/out/build/ext/model2MLIR` and
records it as `MERLIN_M2M_DIR` in `merlin/.env`. A different checkout can be
passed with `--m2m`, but Merlin refuses an off-pin capture by default. Commit
and update the capture pin deliberately before treating a different frontend
revision as reproducible.

## 2. Prove the kernel path first

From `merlin/examples/kodiak` with `MERLIN_TARGET_PATH` set as above:

```sh
./run.sh kernel-test opu_tile_loop --quick
./run.sh op gemm 64 64 64 --matrix-kernel opu_tile_loop
python3 "$HOME/kodiak-work/kodiak-mlir/examples/conv2d_to_opu.py" \
  --merlin "$HOME/kodiak-work/merlin"
```

`kernel-test` compares the C kernel with Merlin's independent int32 reference
over edge tiles, K tails, bias and repeated calls on the OPU Spike model.
The GEMM builds RVV, OPU and stand-in images and checks numerical output. The
convolution example checks that compiler routing actually selects the C file,
records its SHA-256 and compares the shipped image with its scalar twin.
Generated artifacts and routing reports are under `merlin/out/artifacts/`.

To develop a kernel, copy `kernels/opu_tile_loop.c` to a new C file, rename its
exported function to `my_gemm`, keep the `OPU_KERNEL(name)` ABI described in
[README.md](README.md#write-your-own-kernel), and pass the file directly while
iterating:

```sh
./run.sh kernel-test "$HOME/kodiak-work/kodiak-mlir/kernels/my_gemm.c:my_gemm" --quick
./run.sh op gemm 64 64 64 \
  --matrix-kernel "$HOME/kodiak-work/kodiak-mlir/kernels/my_gemm.c:my_gemm"
```

The suffix after `:` is the exported C function. Once stable, add a named
entry and shape constraints to `kernels/kernels.yaml`, then select it by name.
The generated `saturn_opu.h` supplies RTL-derived instruction macros and tile
facts. The template does **not** implement taped-out silicon's partial-N
workaround; keep the generated default for physical images with partial-N
tiles until a replacement passes that hardware-specific gate.

## 3. Obtain and capture a model

Merlin can compile a transferred, receipt-bound capture with
`--capture-bundle /absolute/path/to/bundle`. A fresh capture has its own model,
weight and reference hashes and must be validated independently. The following
commands create fresh captures when the source assets are available.

**SpectFormer.** Clone the source model. Supply a trained SpectFormer-Ti
checkpoint if you want to reproduce trained-model behavior; the loader
otherwise prints `RANDOM INIT` and exercises compilation with random weights.
The checkpoint must be obtained from the model authors or transferred with its
hash; it is not in these repositories.

```sh
cd "$HOME/kodiak-work"
git clone https://github.com/badripatro/SpectFormers.git
cd merlin/examples/kodiak
export SPECTFORMER_DIR="$HOME/kodiak-work/SpectFormers"
export SPECTFORMER_CKPT=/absolute/path/to/checkpoint_best.pth
./run.sh capture spectformer
```

**TinyLlama section.** The loader downloads the named Hugging Face checkpoint
on first use when online; a machine running offline must cache that exact
revision beforehand. The two-layer, 64-token section is the bounded Kodiak
example. The full 22-layer checkpoint can be captured for simulation but its
weights alone exceed physical Kodiak's 512 MiB DRAM.

```sh
CAUSAL_LM_MODEL=TinyLlama/TinyLlama-1.1B-Chat-v1.0 \
CAUSAL_LM_REVISION=fe8a4ea1ffedaf415f4da2f062534de366a451e6 \
CAUSAL_LM_LAYERS=2 CAUSAL_LM_ENTRY=embeds M2M_SEQ=64 \
  ./run.sh capture tinyllama_l2_s64 \
  --loader ../workloads/causal_lm_section/loader.py
```

`capture` writes `merlin/out/artifacts/recaptures/<name>_int8_full` and a W8A8
reference. Merlin uses model2MLIR for the model-to-MLIR step; it then applies
its own W8A8 lowering and grades the target against the captured reference.
See Merlin's [model2MLIR guide](https://github.com/ucb-bar/merlin/blob/feat/OPU-VME/docs/guides/model2mlir.md)
for the `get_model_and_inputs()` loader contract for another model.

## 4. Compile and validate the ELF

From `merlin/examples/kodiak`:

```sh
./run.sh compile spectformer --opu
./run.sh compile tinyllama_l2_s64 --opu
./run.sh compile spectformer --opu --board firesim_kodiak_opu \
  --output "$HOME/kodiak-work/spectformer-firesim.elf"
```

Each `compile` command prints one ELF path and SHA-256 and writes a neighboring
`.build.json` receipt. `--opu` selects the pinned Kodiak prepack schedule;
omit it for a plain-RVV ELF. Use `--matrix-kernel NAME` or
`--matrix-kernel /path/to/kernel.c:function` to select a replacement kernel.
`compile` builds and audits the ELF but does **not** execute it. Run
`./run.sh op ...` for a fast numerical kernel check or
`./run.sh matrix --models spectformer` for a model-level Spike regression.
Read `routing_report.json` to see routed contractions, MAC share and refusals.

`chipyard_kodiak` and `firesim_kodiak_opu` have different board and memory
maps. FireSim needs its separately matched bitstream and queue; the Merlin
[FireSim guide](https://github.com/ucb-bar/merlin/blob/feat/OPU-VME/examples/kodiak/README.md#6-run-on-firesim--the-opus-real-datapath-and-cycles)
gives the submission and collection commands. Physical Kodiak execution is
currently unqualified because the independent chip BootAddrReg positive
control has not passed. A Spike or FireSim pass is not a silicon pass.

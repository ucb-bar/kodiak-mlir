# Kodiak + OPU on FireSim (1 core)

Two patches and one Verilog file. The Kodiak part of this is a port of the existing
`KodiakFireSimConfig` (`raghav-g13/chipyard@kodiak-multi-chip-firesim`, `ad438e3c`,
`generators/chipyard/src/main/scala/config/KodiakConfigs.scala:303`) — active mixins carried over
verbatim and in order, commented-out ones dropped. The CTC variant is deliberately not ported
(`testchipip.ctc` isn't in this checkout). So the only new thing here is the OPU.

## Source revision requirement

The `kodiak-multi-chip-firesim` and `-opu-kernels` branches both pin `generators/saturn` at
`a898bdc`, which **does not contain `OuterProductUnit.scala` at all**, and all three Kodiak configs
there pass `VectorParams.genParams` whose `useOpu` defaults to false. `-opu-kernels` only bumps
firesim/firemarshal to add an `opu-m2-gemm` *workload*; it changes no RTL. The OPU requires
the Saturn source revision below.

So: repin saturn.

    generators/saturn -> miles-rusch-berkeley/saturn-vectors, branch opu-int8
                         ea373800169ce7bbb9ca27ececf74101a7caf9d5   (has OuterProductUnit.scala)

## Apply

    # 1. saturn @ ea37380 (branch opu-int8, already on the remote)
    cd generators/saturn && git apply /path/to/01-saturn-OPUConfigs.patch

    # 2. chipyard @ 6a9b4cd95081100dc932e422cc52777bc9a98b2d
    cd ../.. && git apply /path/to/02-chipyard-firechip-TargetConfigs.patch

Configs added: `KodiakOPUConfig` (2 tiles, OPU on both), `KodiakOPU1CoreConfig`,
`Kodiak1CoreConfig`, plus `FireSimKodiakOPU1CoreConfig` on the firechip side.
Geometry is `WithShuttleVectorUnit(512, 256, VectorParams.opuParams)` — vLen 512, dLen 256, so a
32x32 int8 MACC array = 1024 MAC/cycle, tile edge 64.

**Build the 1-core one.** The 2-core `KodiakOPUConfig` elaborates but has not fit and routed
on the U250 — that's the "1 core only" caveat, not a functional limit.

## The FPGA clock-gate trap (`eicg_passthrough.v`)

Building `FireSimKodiakOPU1CoreConfig` with the stock clock-gate model produces
unclosable hold violations. rocket-chip's `EICG_wrapper` is a latch-and-AND, i.e.
combinational logic in the clock path, so the tools add a *second* global clock net behind it for the
OPU cluster array's 131,585 clock loads — and that net can't be delay-matched to its ungated parent.
Measured on `FireSimOPUV256D128ShuttleConfig`: gated domain 3.79 ns late, **4.207 ns clock skew,
WHS -4.068 ns, 258,620 hold-failing endpoints.** Changing frequency does not help; hold and skew are
both period-independent.

`eicg_passthrough.v` replaces it with a passthrough, selected via `ClockGateModelFile` — no Scala
class and no target RTL changes. `WithEICGPassthrough` is first in the config chain so it overrides
the `WithClockGateModel` that `chipyard.config.AbstractConfig` already applies.

**Keep the patched `OPUConfigs.scala` unchanged when using Merlin's pinned support.** Its
`EICGPassthrough.vsrc` contains a placeholder fallback path, but Merlin checks the entire patched
file against the `saturn_opu_int8` source pin. Editing that line makes the source off-pin.
Set `MERLIN_EICG_PASSTHROUGH_V` in the **remote elaboration process** to the absolute path of
`eicg_passthrough.v` on the build host, and verify that process can read the file before building.
An export only in the local launcher does not survive FireSim's ssh hop. Elaboration `require`s
the file exists and fails loudly if it cannot find it, because otherwise the stock gated clock
model could be selected. If the remote environment cannot be configured, publish a patch with a
concrete path and update Merlin's source pin together before calling that build reproducible.

Ungating is a power/timing tradeoff, not free correctness: `OuterProductCluster.pipe` is assigned
unconditionally, so it does rely on the gate to hold state. The argument that this is safe (the
sequencer holds `clock_enable` high for every in-flight cycle, and `pipe` is re-primed per readout)
is written out in the header of the `.v`. We validate it by re-running the certified OPU microkernel
corpus against this model and requiring bit-identical digests — treat it as validated-by-test, not
proven.

## What we've run on it

On 2026-11-05, the uninstrumented `merlin-compile` SpectFormer image and its plain-RVV control ran
on `alveo_u250_firesim_kodiak_opu_1core` (jobs 3858/3859). The OPU image took 3,531,733,777
target compute cycles versus 6,120,729,118 for RVV, a **1.733x** speedup. Both passed the W8A8
numerical gate (OPU cosine 1.0, max relative error 4.05e-6), and each complete output hash matched
its exact Spike-gated image. The bitstream tar matched its pinned artifact digest; a clean-source
rebuild of that tar has not been demonstrated.

# Kodiak RVV prepack package

This is a Kodiak delivery choice built from the generic RVV schedule in
`rvv-mlir/schedules/impr_tuned_wholemodel_vf_int8`. The `schedule.mlir` bytes are
identical to that parent. `knobs.yaml` adds the RVV-owned
`prepack_weight_layout` feature; Kodiak's FireSim observations and qualification
limits are recorded in `manifest.yaml` and `firesim_evidence.json`.

Select this directory as an explicit Merlin `--package` path when compiling a
Kodiak image. The package targets RVV and can run without selecting the OPU.
The FireSim receipt records an earlier bounded TinyLlama run; it does not
certify a physical Kodiak run or independent TorchAO source parity.

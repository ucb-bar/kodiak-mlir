The Kodiak rig loads and runs images over Ethernet (UDP-TSI) through its FPGA, from `~/Desktop/kodiak` on
the rig host. Follow [the current rig procedure](../../RUNNING.md) before using this delivery.
**The rig is shared: check occupancy and confirm exclusive use** before any load, reset, or FPGA program.
Preserve existing tmux sessions and jobs.

```bash
# health check: the FPGA answers, then the historically passing OPU-direct image reaches the chip
python scripts/py/udp_tsi_host.py ping
python scripts/py/udp_tsi_host.py select-chip 0
python -u scripts/py/udp_tsi_host.py run sw/benchmarks/kodiak_poly_bench_opu_direct.riscv --no-reset
#   must print: 0x1000 readback 0x80000000 — confirmed chip BootAddrReg

# run one image and keep its console
python scripts/py/udp_tsi_host.py --credited 8 --auto run <THIS.elf> | tee <THIS>.console.txt
# only when FPGA reprogramming is intended: this wrapper programs it before every ELF
scripts/bash/execute_program.sh <dir with the ELFs>
```

Without `--auto` the loader stops at "Press Enter to kick hart 0 via MSIP", which looks like a hang.
`readback None`, followed by silence after `Proxy FESVR started.`, fails the chip health gate.
The write ACK and `select-chip` response come from the FPGA and do not prove a chip response.
The current wrapper can print PASS for a loader process that exits with a nonzero status before
its timeout; inspect the actual console, exit status, `DONE`, and numerical output.
Stop before loading a Merlin image and diagnose the chip/FPGA link with the rig owner; do not
assume a reset will fix it. The historical `hello.riscv` log failed at this readback even on the
day the OPU-direct benchmark passed, so it is not a qualified positive control.

### Keep the loader attached for the WHOLE run

This board's console is HTIF, not a self-contained UART: the image writes a word and waits for the host
(the loader's FESVR) to acknowledge it. Stay attached until `DONE`. A detached image looks hung in its very
first print while it is in fact computing and waiting to be read.

Run `vlen_probe.elf` first (expect `vlenb 64`), then each `_debug` image before its plain twin.

# Running images on the Kodiak chip

The Kodiak test rig is **shared**. A load or a reset kills whoever is already running, so the first
step is always to check that the rig is idle.

## 0. Access

The rig is a BWRC desktop that reaches the chip through a NexysVideo FPGA over Ethernet (UDP-TSI).
Ask the rig owner for its host and login. Its SSH port is not open to the wider network, so go through
a BWRC host with your own key (ask the owner to add it; never put a shared password in a file):

```sshconfig
Host kodiak-rig
    HostName <rig host>
    User <rig login>
    ProxyJump <your BWRC jump host>
```

Everything lives in `~/Desktop/kodiak` on the rig.

## 1. Is anyone using it?

Run all of these. If any shows activity, or you are unsure, **stop and ask in Slack**.

```sh
ssh kodiak-rig 'who; \
  ps -eo user,pid,etime,cmd | grep -E "udp_tsi_host|execute_program|arduino|pyuartsi" | grep -v grep; \
  tmux ls 2>/dev/null; screen -ls 2>/dev/null; \
  find ~/Desktop/kodiak -newermt "-10 minutes" -type f | head'
```

Never run an FPGA reset (`udp_tsi_host.py fpga-reset`), reprogram the FPGA, or reset the chip
unless the rig is confirmed idle and you have exclusive use. `udp_tsi_host.py run` resets the chip
by default; its `--no-reset` flag disables that reset. The `execute_program.sh` wrapper programs
the FPGA before **each** ELF, even without `--chip-reset`. Say in Slack when you start and when
you are done.

## 2. Health check, before your own images

```sh
cd ~/Desktop/kodiak
python3 scripts/py/udp_tsi_host.py ping                       # FPGA harness alive (not yet the chip)
python3 scripts/py/udp_tsi_host.py select-chip 0              # FPGA selects chip 0; ACK does not prove chip response
python3 -u scripts/py/udp_tsi_host.py run sw/benchmarks/kodiak_poly_bench_opu_direct.riscv --no-reset
```

After the program loads you must see:

```
0x1000 readback 0x80000000 — confirmed chip BootAddrReg
```

`readback None`, followed by a hang after `Proxy FESVR started.`, fails the chip health gate.
The boot-address write's `ack=ok` confirms only that the FPGA received it; the 0x1000 readback
must confirm the chip response. Stop and diagnose the FPGA link and chip power/reset path before
loading a Merlin image. Only reset or program hardware during an exclusive owner-confirmed window.

Use the OPU-direct benchmark above as the positive control: it reached `0x1000 readback
0x80000000` and passed on 2026-08-18. The historical `hello.riscv` log failed at the readback
even on that day, so `hello.riscv` is not a proven health check. The positive-control loader may
wait for Enter after loading; feed it only after the readback and continue to its PASS result.

On 2026-11-06, fresh JTAG programming of the same bitstream restored FPGA Ethernet carrier and
UDP ping, but the exact August positive-control ELF and uncredited `--no-reset` loader path
still returned `0x1000 readback None`. The PHY state matched the August pass. One Arduino chip-0
reset command returned without a serial completion message, so its effect is unconfirmed. No
Merlin image was loaded. Check chip power, reset, clock and wiring, and account for the Vivado
version change (2024.2 in August, 2024.1 today) before resuming model tests. The positive control
must pass first.

A further bounded check on 2026-11-06 tested a missing host-procedure step without another
reset or FPGA program. The FPGA ping passed, `select-chip 0` was acknowledged, and the same
SHA-matched August OPU-direct ELF and uncredited `--no-reset` loader completed their load.
The boot-address write was acknowledged, but `0x1000` still read as `None`; the loader was
stopped before MSIP or program execution. The chip-select omission alone does not explain the
failure. [The receipt and logs](evidence/bootaddr_select_chip_20261106/receipt.json) bind the
ELF, loader, bitstream file, ping, selection and readback. The 10,650-byte `.text` section loaded
more slowly than in August, but neither run logged an ACK timeout or retry, and the FPGA ACK
does not measure downstream chip completion. That rate difference does not identify a cause.
The same attached tmux sessions remained in place and no loader/reset/programming process
remained afterward. Recheck rig ownership before any further physical action.

## 3. Run Merlin images

Copy a delivery into its own directory on the rig, then run the images in this order: `vlen_probe.elf`
(expect `vlenb 64`, nonzero `mstatus_vs`), then each `_debug` image, then its plain twin.

```sh
scp -r out/artifacts/delivery/<package>/ kodiak-rig:Desktop/kodiak/sw/models/<package>/
ssh kodiak-rig 'cd ~/Desktop/kodiak && \
  python3 scripts/py/udp_tsi_host.py --credited 8 --auto run sw/models/<package>/vlen_probe.elf'
# a single image, keeping the console:
ssh kodiak-rig 'cd ~/Desktop/kodiak && \
  python3 scripts/py/udp_tsi_host.py --credited 8 --auto run sw/models/<package>/<image>.elf' \
  | tee <image>.console.txt
# only when FPGA reprogramming is intended, use the wrapper (retries, timeout):
ssh kodiak-rig 'cd ~/Desktop/kodiak && scripts/bash/execute_program.sh sw/models/<package>/'
```

Without `--auto` the loader stops at "Press Enter to kick hart 0 via MSIP", which looks like a hang.
Keep it attached until the image prints `DONE`: the console is HTIF, so a detached loader makes a working
image look hung.

## 4. Grade

Each delivery carries `grade.py` (numpy only):

```sh
python3 <package>/grade.py <image>.console.txt --model <model>
```

On a log that stopped early it reports the last `STAGE`, any `FAIL` line and failed memory probes.
Keep returned consoles: copy them into Merlin's `out/runs/` with the delivery's `manifest.json`.

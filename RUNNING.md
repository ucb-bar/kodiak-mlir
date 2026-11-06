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

The rig Arduino firmware accepts only `all`, `fpga`, `0`, and `1` as reset commands.
`scripts/bash/arduino_all.sh t` sends an unrecognized command and **does not pulse a reset pin**.
The shell wrapper can return exit zero without any serial reply, so exit status alone does not
confirm a reset. During an exclusive owner-confirmed window, use the intended chip command
(`0` for chip 0) and require its `Completed: 0` reply before treating the command as received;
that reply still does not prove the physical reset pin or chip power/clock state.
[The read-only firmware/wrapper audit](evidence/arduino_reset_command_audit_20261106/receipt.json)
records the exact rig source hashes. It did not send an Arduino command.

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
The boot-address write's `ack=ok` means only that the loader received some UDP packet: this
uncredited path does not check that it was an ACK. A successful 0x1000 readback confirms the chip
response, but `None` alone cannot distinguish a missing response from a host timeout. Stop and
diagnose the host transport and chip/FPGA path before loading a Merlin image. Only reset or
program hardware during an exclusive owner-confirmed window.

Use the OPU-direct benchmark above as the positive control: it reached `0x1000 readback
0x80000000` and passed on 2026-08-18. The historical `hello.riscv` log failed at the readback
even on that day, so `hello.riscv` is not a proven health check. The positive-control loader may
wait for Enter after loading; feed it only after the readback and continue to its PASS result.

On 2026-11-06, fresh JTAG programming of the tracked bitstream restored FPGA Ethernet carrier
and UDP ping. The current ELF at the historically passing OPU-direct path still returned
`0x1000 readback None` with the uncredited `--no-reset` loader. The PHY state matched the
August pass. One Arduino chip-0 reset command returned without a serial completion message,
so its exact effect is unconfirmed; the earlier receipt does not record the command argument.
No Merlin image was loaded. Check chip power, reset, clock and
wiring, and account for the Vivado version change (2024.2 in August, 2024.1 today) before
resuming model tests. The positive control must pass first.

A further bounded check on 2026-11-06 tested a missing host-procedure step without another
reset or FPGA program. The FPGA ping passed, `select-chip 0` returned a UDP response, and the
current OPU-direct ELF and uncredited `--no-reset` loader completed their load. The current ELF
and loader are SHA-bound in the receipt, but no August ELF hash survives to prove byte identity.
The boot-address write returned a UDP response, but `0x1000` still read as `None`; the loader was
stopped before MSIP or program execution. The chip-select omission alone does not explain the
failure. [The receipt and logs](evidence/bootaddr_select_chip_20261106/receipt.json) bind the
ELF, loader, bitstream file, ping, selection and readback. The 10,650-byte `.text` section loaded
more slowly than in August. The loader accepts any UDP response as a write ACK and has no packet
trace, so host timing or protocol behavior also remains possible. An FPGA response does not
measure downstream chip completion; the rate difference does not identify a cause.
The same attached tmux sessions remained in place and no loader/reset/programming process
remained afterward. Recheck rig ownership before any further physical action.

### Offline host readback diagnostic

The SHA-verified loader starts a 2-second 0x1000 read wait, then reduces the next wait to 1
second after **any** response, including a read ACK. Its `write_word64` treats any received UDP
packet as `ack=ok`. An [offline replay](evidence/bootaddr_host_replay_20261106/receipt.json)
of the exact loader reproduces both behaviors: read data at 1.5 seconds succeeds without an ACK,
but returns `None` if an ACK arrives first. Stale ACKs followed promptly by valid data succeed.
The script opens no network socket and requires the loader SHA in the receipt:

```sh
python3 evidence/bootaddr_host_replay_20261106/replay.py --loader /path/to/udp_tsi_host.py
```

This proves a host-side early-timeout path, not that the chip sent a late reply in the physical
run. The saved physical logs have no raw readback packets or timing. Review the loader's ACK
matching and deadline handling, then arrange a timestamped host packet trace with the rig owner
before attributing the physical `None` to the chip or FPGA.

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

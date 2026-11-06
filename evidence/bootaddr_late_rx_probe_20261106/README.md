# Bounded BootAddr late-receive diagnostic

The rig login cannot currently run `tcpdump` or `dumpcap`. This offline generator makes a
diagnostic **copy** of the SHA-verified loader. It leaves its original send/read transaction
unchanged. Only after the `0x1000` read returns `None`, the copy receives for up to three
more seconds, prints at most 16 datagrams with elapsed monotonic time and raw bytes, and exits
with status 86 **before MSIP**. It does not change the rig's tracked loader or prove that a
physical chip reply will arrive. A packet capture remains preferable because it also sees
unread datagrams without changing the process.

The source must be `scripts/py/udp_tsi_host.py` with SHA-256
`857c09058feb653ed71db559c588c63ee602161507731c6a2b39cac5077c5ad4`.
`make_probe.py` refuses any other source or an existing output file. Both scripts can be
copied into an owner-approved diagnostic directory on the rig; do so only during a confirmed
exclusive window. From the rig workspace, with `KODIAK_DIAG_DIR` set to that directory:

```sh
python3 "$KODIAK_DIAG_DIR/make_probe.py" scripts/py/udp_tsi_host.py \
  "$KODIAK_DIAG_DIR/udp_tsi_host_late_rx.py"
python3 "$KODIAK_DIAG_DIR/verify_probe.py" \
  "$KODIAK_DIAG_DIR/udp_tsi_host_late_rx.py"
python3 -u "$KODIAK_DIAG_DIR/udp_tsi_host_late_rx.py" run \
  sw/benchmarks/kodiak_poly_bench_opu_direct.riscv --no-reset
```

First run the **unchanged** positive control and record its exact loader, ELF and FPGA-image
hashes. Use this diagnostic copy only as a separate follow-up in the same owner-approved
state, without changing reset or bitstream in between. If it succeeds at readback, allow the
positive control to complete normally. If it reports `None`, a late datagram appears only
when received inside the extra three-second window. A raw datagram still needs protocol and
transaction attribution; zero logged packets does not prove the chip never replied. Save the
complete console and actual process exit status. Do not rely on
`execute_program.sh`'s PASS label, which can mask a nonzero loader exit. Never treat the
offline gate below as a physical pass.

The offline gate generated a copy from the exact loader source with SHA-256
`91fe70c15cb0d947a264224fa7824545075682b550b8f33195cafa624b3e3695`.
`verify_probe.py` injected a delayed 0x80000000 datagram at 1.5 seconds and confirmed that
the copy logged it and stopped before sending MSIP; it opened no network socket. Wrong-source
SHA rejection was also checked. The late-receive branch has **not** run on the physical rig.

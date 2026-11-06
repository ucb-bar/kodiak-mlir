# Offline host loader patch candidate

This patch addresses two reproducible **host loader** behaviors in the rig's
`scripts/py/udp_tsi_host.py`: a read ACK shortens a 2-second read deadline to
1 second, and a write treats any UDP datagram as a successful ACK. It maintains
one monotonic 2-second read deadline and checks the write ACK's command and
byte count. It does not alter the FPGA or chip.

The patch is bound to unmodified loader SHA-256
`857c09058feb653ed71db559c588c63ee602161507731c6a2b39cac5077c5ad4`.
Its own SHA-256 is `621889cab0be913e0452da912f60961abb9e12be6488922c04db9b9ad16c15d2`.
Apply it only to a **local copy** for an offline comparison:

```sh
patch -o "$TMPDIR/udp_tsi_host_candidate.py" \
  "$TMPDIR/udp_tsi_host.py" loader_deadline_ack.patch
python3 verify_candidate.py \
  --loader "$TMPDIR/udp_tsi_host.py" \
  --candidate "$TMPDIR/udp_tsi_host_candidate.py"
```

The candidate output must hash to
`db9f3b77100910f377c7ebaef85e03e37fa2fed9b0f82ad6d308dcfe66341d5e`.
The verifier uses the real loader functions with a scripted socket and virtual
time; it opens no network connection. Its passing cases show that valid data
at 1.5 seconds after a read ACK or split first word is recovered and an
arbitrary write response is rejected. They do **not** show that the physical
chip sent such data. The earlier physical logs lack packet timestamps.

Keep the rig's tracked loader unchanged. During a new exclusive rig window,
capture timestamped raw `0x1000` write/read datagrams with the current loader
first. If the chip sends valid read data after the loader's early timeout,
this candidate is a justified next physical trial. A missing chip response
requires a different recovery path. See [RUNNING.md](../../RUNNING.md) and the
[baseline replay](../bootaddr_host_replay_20261106/receipt.json).

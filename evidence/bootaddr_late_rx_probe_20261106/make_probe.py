"""Make a bounded BootAddr receive diagnostic from the exact rig loader.

This script only reads and writes local files. The generated copy sends the
original transactions unchanged. If the 0x1000 read returns None, it listens
for at most three more seconds, logs up to 16 datagrams, and exits before MSIP.
"""

import argparse
import ast
import hashlib
from pathlib import Path

SOURCE_SHA256 = "857c09058feb653ed71db559c588c63ee602161507731c6a2b39cac5077c5ad4"
ANCHOR = "    readback = read_word64(sock, dest, 0x1000)\n"
REPLACEMENT = """    bootaddr_read_started = time.monotonic()
    readback = read_word64(sock, dest, 0x1000)
    if readback is None:
        print("BOOTADDR_LATE_RX probe: original read returned None; listening 3.0s before MSIP", flush=True)
        deadline = time.monotonic() + 3.0
        seen = 0
        while seen < 16:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            sock.settimeout(remaining)
            try:
                packet, peer = sock.recvfrom(4096)
            except socket.timeout:
                break
            seen += 1
            elapsed = time.monotonic() - bootaddr_read_started
            print(f"BOOTADDR_LATE_RX elapsed_s={elapsed:.6f} peer={peer} bytes={len(packet)} hex={packet.hex()} ack={parse_ack(packet) is not None}", flush=True)
        print(f"BOOTADDR_LATE_RX complete packets={seen}; stopping before MSIP", flush=True)
        raise SystemExit(86)
"""


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "loader", type=Path, help="unchanged rig scripts/py/udp_tsi_host.py"
    )
    parser.add_argument("output", type=Path, help="new diagnostic copy; must not exist")
    args = parser.parse_args()
    source = args.loader.read_bytes()
    if sha(source) != SOURCE_SHA256:
        raise SystemExit(f"refusing loader SHA {sha(source)}; expected {SOURCE_SHA256}")
    if args.output.exists():
        raise SystemExit(f"refusing to overwrite {args.output}")
    body = source.decode("utf-8")
    if body.count(ANCHOR) != 1:
        raise SystemExit("refusing loader without unique BootAddr read anchor")
    revised = body.replace(ANCHOR, REPLACEMENT).encode("utf-8")
    ast.parse(revised, filename=str(args.output))
    args.output.write_bytes(revised)
    print(
        f"source_sha256={sha(source)} diagnostic_sha256={sha(revised)} output={args.output}"
    )


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Replay the SHA-verified Kodiak BootAddrReg loader against an offline UDP socket.

This imports the loader's real encode, write and read functions, but supplies a
scripted socket. It never opens a socket or contacts the rig. Times are virtual.
"""

import argparse
import hashlib
import importlib.util
import json
import socket
import struct
import sys
from pathlib import Path

LOADER_SHA256 = "857c09058feb653ed71db559c588c63ee602161507731c6a2b39cac5077c5ad4"
DEST = ("127.0.0.1", 7000)  # Recorded by the mock; never used for networking.
BOOT_ADDR = 0x1000
BOOT_VALUE = 0x80000000


def load_verified_loader(path):
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != LOADER_SHA256:
        raise ValueError(f"loader SHA-256 mismatch: expected {LOADER_SHA256}, got {digest}")
    spec = importlib.util.spec_from_file_location("verified_kodiak_udp_tsi_host", path)
    if spec is None or spec.loader is None:
        raise ValueError("cannot import verified loader")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MockSocket:
    def __init__(self, loader, read_replies=(), write_reply="ack"):
        self.loader = loader
        self.read_replies = read_replies
        self.write_reply = write_reply
        self.now = 0.0
        self.timeout = None
        self.pending = []
        self.packets = []

    def ack(self, command):
        # The parser's 12-byte FPGA ACK: magic, byte count, aux, command word.
        return struct.pack(">IHHI", self.loader.ACK_MAGIC, 8, 0, command)

    def settimeout(self, timeout):
        self.timeout = timeout

    def sendto(self, payload, dest):
        words = struct.unpack("<" + "I" * (len(payload) // 4), payload)
        kind = "write" if words[0] & 1 else "read"
        self.packets.append({"t_ms": round(self.now * 1000), "dir": "tx", "kind": kind,
                             "hex": payload.hex()})
        if kind == "write":
            if self.write_reply == "ack":
                self.pending.append((self.now, self.ack(words[0]), "write_ack"))
            elif self.write_reply == "arbitrary":
                self.pending.append((self.now, b"garbage!", "arbitrary_write_reply"))
        else:
            for delay, reply, label in self.read_replies:
                # None means a correctly identified ACK for this read command.
                self.pending.append((self.now + delay,
                                     self.ack(words[0]) if reply is None else reply, label))

    def recvfrom(self, count):
        horizon = self.now + (self.timeout or 0)
        ready = [(index, event) for index, event in enumerate(self.pending)
                 if event[0] <= horizon]
        if not ready:
            self.now = horizon
            raise socket.timeout()
        index, (at, payload, label) = min(ready, key=lambda item: (item[1][0], item[0]))
        self.pending.pop(index)
        self.now = max(self.now, at)
        self.packets.append({"t_ms": round(self.now * 1000), "dir": "rx",
                             "kind": label, "hex": payload.hex(),
                             "during_flush": self.timeout == 0})
        return payload[:count], DEST


def replay(loader, name, expected, replies=(), pre_read=(), write_reply="ack"):
    sock = MockSocket(loader, replies, write_reply)
    write_ok = loader.write_word64(sock, DEST, BOOT_ADDR, BOOT_VALUE)
    for delay, payload, label in pre_read:
        sock.pending.append((sock.now + delay, payload, label))
    readback = loader.read_word64(sock, DEST, BOOT_ADDR)
    sent = [p for p in sock.packets if p["dir"] == "tx"]
    if len(sent) != 2 or any(bytes.fromhex(p["hex"])[4:8] != b"\x00\x10\x00\x00"
                             for p in sent):
        raise AssertionError(f"{name}: loader did not send expected 0x1000 transactions")
    ok = write_ok is True and readback == expected
    return {"case": name, "pass": ok, "write_ok": write_ok,
            "readback": None if readback is None else f"0x{readback:016x}",
            "virtual_elapsed_ms": round(sock.now * 1000), "packets": sock.packets,
            "pending": [{"at_ms": round(at * 1000), "kind": label, "hex": payload.hex()}
                        for at, payload, label in sock.pending]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--loader", type=Path, required=True,
                        help="local copy of the rig loader; exact SHA-256 is required")
    args = parser.parse_args()
    try:
        loader = load_verified_loader(args.loader)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    data = struct.pack("<Q", BOOT_VALUE)
    probe = MockSocket(loader)
    stale_ack = probe.ack(0xDEAD)
    cases = [
        replay(loader, "valid", BOOT_VALUE, [(0, None, "read_ack"), (0, data, "read_data")]),
        replay(loader, "stale_ack_buffered_before_read", BOOT_VALUE,
               [(0, data, "read_data")], [(0, stale_ack, "stale_ack")]),
        replay(loader, "stale_ack_after_read_request", BOOT_VALUE,
               [(0, stale_ack, "stale_ack"), (0.01, data, "read_data")]),
        replay(loader, "no_ack_data_1p5s", BOOT_VALUE, [(1.5, data, "read_data")]),
        replay(loader, "ack_then_data_1p5s", None,
               [(0, None, "read_ack"), (1.5, data, "read_data")]),
        replay(loader, "ack_then_data_2p1s", None,
               [(0, None, "read_ack"), (2.1, data, "late_read_data")]),
        replay(loader, "no_read_response", None),
        replay(loader, "arbitrary_write_reply", BOOT_VALUE,
               [(0, data, "read_data")], write_reply="arbitrary"),
    ]
    findings = {"read_ack_shortens_wait": cases[3]["readback"] is not None
                and cases[4]["readback"] is None,
                "write_accepts_arbitrary_udp_reply": cases[7]["write_ok"] is True}
    verdict = {"schema": "kodiak_bootaddr_host_replay_v1", "loader_sha256": LOADER_SHA256,
               "network": "offline scripted socket; no rig traffic", "timebase": "virtual milliseconds",
               "all_expected": all(case["pass"] for case in cases), "host_findings": findings,
               "cases": cases,
               "limits": ["Physical readback packet timing and contents were not captured.",
                          "A synthetic late response does not prove the chip sent one."]}
    print(json.dumps(verdict, separators=(",", ":")))
    return 0 if verdict["all_expected"] and all(findings.values()) else 1


if __name__ == "__main__":
    sys.exit(main())

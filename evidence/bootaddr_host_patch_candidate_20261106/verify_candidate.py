#!/usr/bin/env python3
"""Compare the exact rig loader with a proposed host-only fix; no network I/O."""

import argparse
import hashlib
import importlib.util
import json
import struct
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
REPLAY = HERE.parent / "bootaddr_host_replay_20261106" / "replay.py"
BASE_SHA = "857c09058feb653ed71db559c588c63ee602161507731c6a2b39cac5077c5ad4"
PATCH_SHA = "621889cab0be913e0452da912f60961abb9e12be6488922c04db9b9ad16c15d2"
CANDIDATE_SHA = "db9f3b77100910f377c7ebaef85e03e37fa2fed9b0f82ad6d308dcfe66341d5e"


def checked(path, expected):
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != expected:
        raise ValueError(f"{path}: SHA-256 {actual} != {expected}")


def import_file(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--loader", type=Path, required=True, help="exact, unmodified rig loader copy")
    parser.add_argument("--candidate", type=Path, required=True, help="patch output; never the rig loader")
    args = parser.parse_args()
    try:
        checked(args.loader, BASE_SHA)
        checked(HERE / "loader_deadline_ack.patch", PATCH_SHA)
        checked(args.candidate, CANDIDATE_SHA)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))

    replay = import_file("kodiak_host_replay", REPLAY)
    baseline = import_file("kodiak_loader_baseline", args.loader)
    candidate = import_file("kodiak_loader_candidate", args.candidate)
    value = 0x80000000
    data = struct.pack("<Q", value)

    class ProtocolSocket(replay.MockSocket):
        def sendto(self, payload, dest):
            super().sendto(payload, dest)
            if struct.unpack_from("<I", payload)[0] & 1 and self.write_reply == "ack":
                at, _, label = self.pending[-1]
                command = struct.unpack_from("<I", payload)[0]
                self.pending[-1] = (
                    at,
                    struct.pack(">IHHI", self.loader.ACK_MAGIC, len(payload), 0, command),
                    label,
                )

    def run(loader, replies, write_reply="ack"):
        sock = ProtocolSocket(loader, replies, write_reply)
        loader.time = SimpleNamespace(monotonic=lambda: sock.now)
        write_ok = loader.write_word64(sock, replay.DEST, replay.BOOT_ADDR, value)
        readback = loader.read_word64(sock, replay.DEST, replay.BOOT_ADDR)
        return {"write_ok": write_ok, "readback": readback, "elapsed_ms": round(sock.now * 1000)}

    scenarios = {
        "ack_then_data_1500ms": ([(0, None, "read_ack"), (1.5, data, "read_data")], "ack"),
        "split_data_1500ms": ([(0, data[:4], "low"), (1.5, data[4:], "high")], "ack"),
        "arbitrary_write_reply": ([(0, data, "read_data")], "arbitrary"),
        "no_read_response": ([], "ack"),
    }
    results = {
        name: {
            "baseline": run(baseline, replies, write_reply),
            "candidate": run(candidate, replies, write_reply),
        }
        for name, (replies, write_reply) in scenarios.items()
    }
    assert results["ack_then_data_1500ms"]["baseline"]["readback"] is None
    assert results["ack_then_data_1500ms"]["candidate"]["readback"] == value
    assert results["split_data_1500ms"]["baseline"]["readback"] is None
    assert results["split_data_1500ms"]["candidate"]["readback"] == value
    assert results["arbitrary_write_reply"]["baseline"]["write_ok"] is True
    assert results["arbitrary_write_reply"]["candidate"]["write_ok"] is False
    assert results["no_read_response"]["candidate"]["readback"] is None
    print(json.dumps({"schema": "kodiak_loader_offline_patch_comparison_v1", "network": "none", "results": results}))


if __name__ == "__main__":
    main()

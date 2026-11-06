"""Offline check: a late BootAddr datagram is logged and MSIP is not sent."""

import argparse
import contextlib
import importlib.util
import io
import struct
from pathlib import Path
from types import SimpleNamespace


class FakeSocket:
    def __init__(self) -> None:
        self.now = 0.0
        self.timeout = None
        self.received = False

    def settimeout(self, seconds: float) -> None:
        self.timeout = seconds

    def recvfrom(self, limit: int):
        assert limit >= 8
        assert self.timeout is not None
        if not self.received:
            assert self.timeout >= 1.5
            self.now = 1.5
            self.received = True
            return struct.pack("<Q", 0x80000000), ("192.168.1.10", 7000)
        self.now += self.timeout
        raise TimeoutError


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("diagnostic_copy", type=Path)
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location(
        "kodiak_diag_loader", args.diagnostic_copy
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    sock = FakeSocket()
    writes = []
    module.time = SimpleNamespace(monotonic=lambda: sock.now)
    module.write_word64 = lambda _sock, _dest, addr, value: (
        writes.append((addr, value)) or True
    )
    module.read_word64 = lambda _sock, _dest, addr: None
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        try:
            module.kick_msip_and_run_fesvr(
                sock, ("192.168.1.10", 7000), 0x80001000, 0x80001008
            )
        except SystemExit as exc:
            assert exc.code == 86, exc.code
        else:
            raise AssertionError("diagnostic did not stop before MSIP")
    log = output.getvalue()
    assert "BOOTADDR_LATE_RX elapsed_s=1.500000" in log, log
    assert "hex=0000008000000000" in log, log
    assert "BOOTADDR_LATE_RX complete packets=1; stopping before MSIP" in log, log
    assert writes == [(0x80001000, 0), (0x1000, 0x80000000)], writes
    print("offline late-RX/abort gate PASS; no network socket opened")


if __name__ == "__main__":
    main()

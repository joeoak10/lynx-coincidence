#!/usr/bin/env python3
"""
hello_lynx.py — first Python 3 script for the Mirion/Canberra Lynx.

Copy this file to the lab PC, e.g.:
    C:\\Lynx\\SDK\\PythonExamples\\Examples\\hello_lynx.py

Run (Python 3.13 is fine):
    cd C:\\Lynx\\SDK\\PythonExamples\\Examples
    python hello_lynx.py

Default = EMULATE (no Lynx required, HV is never touched).
When the box is on the network:
    python hello_lynx.py --real
    then type the Lynx IP when asked.

This script does NOT enable high voltage.
"""

from __future__ import annotations

import argparse
import csv
import random
import socket
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


# ---------------------------------------------------------------------------
# Fake Lynx — same method names as the SDK Device class
# ---------------------------------------------------------------------------
@dataclass
class FakeEvent:
    channel: int
    timestamp_ns: int
    input_id: int


class FakeListData:
    def __init__(self, events: list[FakeEvent]):
        self._events = events
        self._start = datetime.now(timezone.utc)

    def getStartTime(self):
        return self._start

    def getLiveTime(self):
        return 1_000_000

    def getRealTime(self):
        return 1_000_000

    def getTimebase(self):
        return 8

    def getFlags(self):
        return 0

    def getEvents(self):
        return self._events


class FakeLynx:
    """Stand-in so you can write coincidence code before beam time."""

    def __init__(self):
        self.connected = False
        self.locked = False
        self.params: dict = {}
        self._t0_ns = time.time_ns()

    def open(self, client: str, device: str) -> None:
        print(f"[emulate] open(client={client!r}, device={device!r})")
        self.connected = True
        self.params["Network_MachineName"] = "FAKE-LYNX"

    def lock(self, user: str, password: str, inp: int) -> None:
        print(f"[emulate] lock(user={user!r}, input={inp})")
        self.locked = True

    def setParameter(self, code, value, inp: int) -> None:
        self.params[(code, inp)] = value

    def getParameter(self, code, inp: int = 0):
        if str(code).endswith("MachineName") or code == "Network_MachineName":
            return self.params.get("Network_MachineName", "FAKE-LYNX")
        return self.params.get((code, inp), 0)

    def control(self, cmd, inp: int) -> None:
        print(f"[emulate] control({cmd!r}, input={inp})")

    def getListData(self, inp: int) -> FakeListData:
        events = []
        t = time.time_ns() - self._t0_ns
        for i in range(8):
            events.append(
                FakeEvent(
                    channel=random.randint(400, 1800),
                    timestamp_ns=t + i * 50_000,
                    input_id=inp,
                )
            )
        return FakeListData(events)

    def close(self) -> None:
        self.connected = False


def try_real_lynx(ip: str, inp: int):
    """
    Load vendor DataTypes if present. Will fail on Python 3 until those
    files are ported — that error is useful, not a broken PC.
    """
    here = Path(__file__).resolve().parent
    datatypes = here.parent / "DataTypes"
    if datatypes.is_dir():
        sys.path.insert(0, str(datatypes))

    try:
        from DeviceFactory import DeviceFactory  # type: ignore
        from ParameterCodes import ParameterCodes  # type: ignore
    except SyntaxError as exc:
        raise SystemExit(
            "The official DataTypes library is Python 2 and cannot be imported "
            f"on Python {sys.version.split()[0]}.\n"
            "Stay in emulate mode (default) until we port DataTypes, "
            "or install Python 2.7 only to run ExampleList.py.\n"
            f"Import error: {exc}"
        ) from exc
    except ImportError as exc:
        raise SystemExit(
            f"Could not import Lynx DataTypes from {datatypes}.\n"
            "Put hello_lynx.py in C:\\Lynx\\SDK\\PythonExamples\\Examples\\\n"
            f"{exc}"
        ) from exc

    lynx = DeviceFactory.createInstance(DeviceFactory.DeviceInterface.IDevice)
    lynx.open("", ip)
    name = lynx.getParameter(ParameterCodes.Network_MachineName, 0)
    print(f"Connected to real Lynx: {name}")
    lynx.lock("administrator", "password", inp)
    # Intentionally no Input_VoltageStatus / HV here.
    return lynx, name


def save_csv(path: Path, events) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["channel", "timestamp_ns", "input_id"])
        for ev in events:
            if hasattr(ev, "channel"):
                w.writerow([ev.channel, ev.timestamp_ns, ev.input_id])
            else:
                w.writerow([ev, "", ""])
    print(f"Wrote {path}")


def main() -> int:
    p = argparse.ArgumentParser(description="Python 3 hello for Canberra Lynx")
    p.add_argument(
        "--real",
        action="store_true",
        help="Talk to a real Lynx (needs ported or Py2 DataTypes + IP)",
    )
    p.add_argument("--ip", default="", help="Lynx IP, otherwise prompted")
    p.add_argument("--input", type=int, default=1, help="Lynx input number")
    p.add_argument(
        "--out",
        default="hello_lynx_events.csv",
        help="CSV of list-like events",
    )
    args = p.parse_args()

    print(f"Python {sys.version.split()[0]}  |  HV will NOT be enabled")

    if args.real:
        ip = args.ip or input("Lynx IP (a.b.c.d): ").strip()
        try:
            socket.gethostbyname(ip)
        except OSError:
            print(f"Not a usable host name/IP: {ip}")
            return 1
        lynx, name = try_real_lynx(ip, args.input)
    else:
        lynx = FakeLynx()
        lynx.open("", "127.0.0.1")
        lynx.lock("emulate", "", args.input)
        name = lynx.getParameter("Network_MachineName", 0)
        print(f"Emulated device name: {name}")

    lynx.control("Clear", args.input)
    lynx.control("Start", args.input)
    bundle = lynx.getListData(args.input)
    events = list(bundle.getEvents())
    print(f"Got {len(events)} events (live_us={bundle.getLiveTime()})")
    for ev in events[:5]:
        print(f"  {ev}")
    if len(events) > 5:
        print("  ...")

    lynx.control("Stop", args.input)
    out = Path(args.out)
    save_csv(out, events)
    print("Done. Next: keep emulate on until the Lynx IP is known, then --real.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

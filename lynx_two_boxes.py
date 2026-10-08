#!/usr/bin/env python3
"""
Two silicon detectors on TWO Lynxes.

Lynx A, input 1 = proton detector.
Lynx B, input 1 = alpha detector.
A sync cable must join the Ext Sync BNCs, and --sync must be passed,
or the two clocks are not the same and coincidence is meaningless.

Fake run:
    python lynx_two_boxes.py --label W_tile_test

Real run, HV off, sync enabled (only after the cable is in):
    python lynx_two_boxes.py --real --ip-a 130.183.43.184 --ip-b 130.183.43.185 --sync --label p_alpha
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

IP_A = "130.183.43.184"   # proton Lynx, e.g. IBIS Prot
IP_B = "130.183.43.185"   # alpha Lynx, e.g. S Alpha — confirm both are free
INPUT = 1
PRESET_S = 2.0
POLL_S = 2.0
COINC_WINDOW_NS = 200_000
ENABLE_HV = False
DATA_ROOT = Path(__file__).resolve().parent / "runs"
SAMPLE_LABEL = "W_tile_two_lynx"
LYNX_USER = "administrator"
LYNX_PASSWORD = "password"

INPUT_MODE = 60
PRESET_OPTIONS = 38
PRESET_REAL = 40
NETWORK_MACHINE_NAME = 81
INPUT_VOLTAGE_STATUS = 161
INPUT_EXTERNAL_SYNC_MODE = 264
INPUT_EXTERNAL_SYNC_STATUS = 263
SYNC_MASTER = 1
SYNC_SLAVE = 0
SYNC_DISABLED = 0
SYNC_ENABLED = 1
MODE_TLIST = 5
PRESET_REAL_TIME = 1
CMD_START, CMD_STOP, CMD_CLEAR = 3, 4, 5


@dataclass
class Event:
    channel: int
    timestamp_ns: int
    input_id: int
    source: str
    lynx_ip: str


class FakeLynx:
    def __init__(self, role: str):
        self.role = role

    def open(self, client, device):
        print(f"[fake] open {self.role} {device}")

    def lock(self, user, password, inp):
        print(f"[fake] lock {self.role} input {inp}")

    def setParameter(self, code, value, inp):
        pass

    def getParameter(self, code, inp=0):
        return f"FAKE-{self.role}" if code == NETWORK_MACHINE_NAME else 0

    def control(self, cmd, inp):
        print(f"[fake] {self.role} cmd {cmd}")

    def getListData(self, inp):
        t0 = 1_000_000_000_000
        out = []
        for i in range(40):
            ts = t0 + i * 1_000_000
            if self.role == "proton":
                out.append(Event(random.randint(2000, 3500), ts, INPUT, "proton", IP_A))
            elif random.random() < 0.7:
                out.append(Event(random.randint(400, 900), ts + 80_000, INPUT, "alpha", IP_B))
        return out

    def close(self):
        pass


def open_real(ip: str):
    here = Path(__file__).resolve().parent
    sys.path.insert(0, str(here.parent / "DataTypes"))
    from DeviceFactory import DeviceFactory  # type: ignore

    lynx = DeviceFactory.createInstance(DeviceFactory.DeviceInterface.IDevice)
    lynx.open("", ip)
    return lynx


def setup(lynx, preset_s: float, hv: bool, sync_mode: int, sync_on: bool) -> None:
    lynx.lock(LYNX_USER, LYNX_PASSWORD, INPUT)
    lynx.control(CMD_STOP, INPUT)
    lynx.setParameter(INPUT_MODE, MODE_TLIST, INPUT)
    lynx.setParameter(PRESET_OPTIONS, PRESET_REAL_TIME, INPUT)
    lynx.setParameter(PRESET_REAL, float(preset_s), INPUT)
    # Master on the proton Lynx, slave on the alpha Lynx. Cable required.
    lynx.setParameter(INPUT_EXTERNAL_SYNC_MODE, sync_mode, INPUT)
    lynx.setParameter(INPUT_EXTERNAL_SYNC_STATUS, SYNC_ENABLED if sync_on else SYNC_DISABLED, INPUT)
    lynx.control(CMD_CLEAR, INPUT)
    if hv:
        print("WARNING: HV requested")
        lynx.setParameter(INPUT_VOLTAGE_STATUS, True, INPUT)


def acquire(lynx_a, lynx_b, ip_a: str, ip_b: str, preset_s: float, hv: bool, sync_on: bool) -> list[Event]:
    lynx_a.open("", ip_a)
    lynx_b.open("", ip_b)
    print("A:", lynx_a.getParameter(NETWORK_MACHINE_NAME, 0), ip_a)
    print("B:", lynx_b.getParameter(NETWORK_MACHINE_NAME, 0), ip_b)
    setup(lynx_a, preset_s, hv, SYNC_MASTER, sync_on)
    setup(lynx_b, preset_s, hv, SYNC_SLAVE, sync_on)
    lynx_a.control(CMD_START, INPUT)
    lynx_b.control(CMD_START, INPUT)
    events: list[Event] = []
    fake = isinstance(lynx_a, FakeLynx)
    deadline = time.monotonic() + (0.0 if fake else preset_s)
    while True:
        for ev in lynx_a.getListData(INPUT):
            ev.source = "proton"
            ev.lynx_ip = ip_a
            events.append(ev)
        for ev in lynx_b.getListData(INPUT):
            ev.source = "alpha"
            ev.lynx_ip = ip_b
            events.append(ev)
        if fake or time.monotonic() >= deadline:
            break
        time.sleep(POLL_S)
    lynx_a.control(CMD_STOP, INPUT)
    lynx_b.control(CMD_STOP, INPUT)
    events.sort(key=lambda e: e.timestamp_ns)
    print(f"Events: {len(events)}")
    return events


def pairs(events: list[Event]) -> list[tuple[Event, Event]]:
    a = [e for e in events if e.source == "proton"]
    b = [e for e in events if e.source == "alpha"]
    out = []
    j = 0
    for ea in a:
        while j < len(b) and b[j].timestamp_ns < ea.timestamp_ns - COINC_WINDOW_NS:
            j += 1
        k = j
        while k < len(b) and b[k].timestamp_ns <= ea.timestamp_ns + COINC_WINDOW_NS:
            out.append((ea, b[k]))
            k += 1
    return out


def main() -> int:
    p = argparse.ArgumentParser(description="Two Lynxes, one silicon each")
    p.add_argument("--real", action="store_true")
    p.add_argument("--ip-a", default=IP_A)
    p.add_argument("--ip-b", default=IP_B)
    p.add_argument("--label", default=SAMPLE_LABEL)
    p.add_argument("--preset", type=float, default=PRESET_S)
    p.add_argument("--hv", action="store_true")
    p.add_argument("--sync", action="store_true", help="enable Ext Sync; cable must already be connected")
    args = p.parse_args()
    if args.real:
        lynx_a, lynx_b = open_real(args.ip_a), open_real(args.ip_b)
    else:
        lynx_a, lynx_b = FakeLynx("proton"), FakeLynx("alpha")
    try:
        events = acquire(lynx_a, lynx_b, args.ip_a, args.ip_b, args.preset, ENABLE_HV or args.hv, args.sync)
    finally:
        lynx_a.close()
        lynx_b.close()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    folder = DATA_ROOT / f"{stamp}_{args.label}"
    folder.mkdir(parents=True, exist_ok=False)
    with (folder / "events.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["channel", "timestamp_ns", "input_id", "source", "lynx_ip"])
        w.writeheader()
        for e in events:
            w.writerow(asdict(e))
    found = pairs(events)
    (folder / "run_meta.json").write_text(json.dumps({
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "layout": "two Lynxes, input 1 on each",
        "ip_proton": args.ip_a,
        "ip_alpha": args.ip_b,
        "sync_enabled": args.sync,
        "n_events": len(events),
        "n_pairs": len(found),
        "hv": ENABLE_HV or args.hv,
    }, indent=2), encoding="utf-8")
    print(f"Pairs: {len(found)}")
    print("Wrote", folder)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# New PC:
# cd C:\lynx-coincidence
# py -3.13 -m venv .venv
# .venv\Scripts\activate.bat
# python lynx_two_boxes.py --label W_tile_test
# python lynx_two_boxes.py --real --ip-a 130.183.43.184 --ip-b 130.183.43.185 --sync --label p_alpha

#!/usr/bin/env python3
"""
Two silicon detectors on ONE Lynx (input 1 = proton, input 2 = alpha).

Use this only if the supervisor confirms that Lynx has two analog inputs.

Fake run (no hardware):
    python lynx_one_box.py --label W_tile_test

Real run, HV off:
    python lynx_one_box.py --real --ip 130.183.43.184 --label IBIS_Prot
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

# Proton detector on input 1, alpha detector on input 2, same IP.
LYNX_IP = "130.183.43.184"
INPUT_A = 1
INPUT_B = 2
PRESET_S = 2.0
POLL_S = 2.0
COINC_WINDOW_NS = 200_000
ENABLE_HV = False
DATA_ROOT = Path(__file__).resolve().parent / "runs"
SAMPLE_LABEL = "W_tile_one_lynx"
LYNX_USER = "administrator"
LYNX_PASSWORD = "password"

INPUT_MODE = 60
PRESET_OPTIONS = 38
PRESET_REAL = 40
NETWORK_MACHINE_NAME = 81
INPUT_VOLTAGE_STATUS = 161
MODE_TLIST = 5
PRESET_REAL_TIME = 1
CMD_START, CMD_STOP, CMD_CLEAR = 3, 4, 5


@dataclass
class Event:
    channel: int
    timestamp_ns: int
    input_id: int
    source: str


class FakeLynx:
    def open(self, client, device):
        print(f"[fake] open {device}")

    def lock(self, user, password, inp):
        print(f"[fake] lock input {inp}")

    def setParameter(self, code, value, inp):
        pass

    def getParameter(self, code, inp=0):
        return "FAKE-ONE-LYNX" if code == NETWORK_MACHINE_NAME else 0

    def control(self, cmd, inp):
        print(f"[fake] cmd {cmd} input {inp}")

    def getListData(self, inp):
        t0 = 1_000_000_000_000
        out = []
        for i in range(40):
            ts = t0 + i * 1_000_000
            if inp == INPUT_A:
                out.append(Event(random.randint(2000, 3500), ts, inp, "proton"))
            elif random.random() < 0.7:
                out.append(Event(random.randint(400, 900), ts + 80_000, inp, "alpha"))
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


def setup_input(lynx, inp: int, preset_s: float, hv: bool) -> None:
    lynx.lock(LYNX_USER, LYNX_PASSWORD, inp)
    lynx.control(CMD_STOP, inp)
    lynx.setParameter(INPUT_MODE, MODE_TLIST, inp)
    lynx.setParameter(PRESET_OPTIONS, PRESET_REAL_TIME, inp)
    lynx.setParameter(PRESET_REAL, float(preset_s), inp)
    lynx.control(CMD_CLEAR, inp)
    if hv:
        print("WARNING: HV on input", inp)
        lynx.setParameter(INPUT_VOLTAGE_STATUS, True, inp)


def acquire(lynx, ip: str, preset_s: float, hv: bool) -> list[Event]:
    lynx.open("", ip)
    print("Connected:", lynx.getParameter(NETWORK_MACHINE_NAME, 0))
    for inp in (INPUT_A, INPUT_B):
        setup_input(lynx, inp, preset_s, hv)
    for inp in (INPUT_A, INPUT_B):
        lynx.control(CMD_START, inp)
    events: list[Event] = []
    fake = isinstance(lynx, FakeLynx)
    deadline = time.monotonic() + (0.0 if fake else preset_s)
    while True:
        for inp, role in ((INPUT_A, "proton"), (INPUT_B, "alpha")):
            for ev in lynx.getListData(inp):
                ev.source = role
                events.append(ev)
        if fake or time.monotonic() >= deadline:
            break
        time.sleep(POLL_S)
    for inp in (INPUT_A, INPUT_B):
        lynx.control(CMD_STOP, inp)
    events.sort(key=lambda e: e.timestamp_ns)
    print(f"Events: {len(events)}")
    return events


def pairs(events: list[Event]) -> list[tuple[Event, Event]]:
    a = [e for e in events if e.input_id == INPUT_A]
    b = [e for e in events if e.input_id == INPUT_B]
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


def save(folder: Path, events: list[Event], meta: dict) -> None:
    folder.mkdir(parents=True, exist_ok=False)
    with (folder / "events.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["channel", "timestamp_ns", "input_id", "source"])
        w.writeheader()
        for e in events:
            w.writerow(asdict(e))
    (folder / "run_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print("Wrote", folder)


def main() -> int:
    p = argparse.ArgumentParser(description="One Lynx, two silicon inputs")
    p.add_argument("--real", action="store_true")
    p.add_argument("--ip", default=LYNX_IP)
    p.add_argument("--label", default=SAMPLE_LABEL)
    p.add_argument("--preset", type=float, default=PRESET_S)
    p.add_argument("--hv", action="store_true")
    args = p.parse_args()
    lynx = open_real(args.ip) if args.real else FakeLynx()
    try:
        events = acquire(lynx, args.ip, args.preset, ENABLE_HV or args.hv)
    finally:
        lynx.close()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    folder = DATA_ROOT / f"{stamp}_{args.label}"
    save(folder, events, {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "layout": "one Lynx, input 1 proton, input 2 alpha",
        "ip": args.ip,
        "n_events": len(events),
        "n_pairs": len(pairs(events)),
        "hv": ENABLE_HV or args.hv,
    })
    print(f"Pairs: {len(pairs(events))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# New PC:
# cd C:\lynx-coincidence
# py -3.13 -m venv .venv
# .venv\Scripts\activate.bat
# python lynx_one_box.py --label W_tile_test
# python lynx_one_box.py --real --ip 130.183.43.184 --label IBIS_Prot

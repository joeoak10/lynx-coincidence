#!/usr/bin/env python3
"""
==============================================================================
lynx_measure.py  -  FINAL measurement script, two Lynx boxes at once
==============================================================================
Hardware this script is written for (IPP Garching rack):
    IBIS Prot  130.183.43.184   input 1   = PROTON silicon detector  (sync MASTER)
    S Alpha    130.183.43.185   input 1   = ALPHA  silicon detector  (sync SLAVE)
    Ext Sync BNC of IBIS Prot  ->  Ext Sync BNC of S Alpha   (cable required)

What one run does:
    1. connects to both Lynx boxes, sets time-stamped list mode, preset time
    2. starts both, polls both every POLL_S seconds, saves events as it goes
    3. stops both, sorts events, finds proton-alpha coincidences in software
    4. writes everything into  runs\\<timestamp>_<label>\\

Files written per run:
    events.csv       raw events of both detectors          <-- THE RAW DATA
    coinc_pairs.csv  every proton-alpha pair inside the window
    run_meta.json    all settings of the run
    summary.txt      counts, rates, pairs, dt peak, spectrum peaks
    spectra.png      quick-look plots (needs matplotlib)

High voltage is NEVER switched on by this script unless you pass --hv AND
type YES. Leave it alone: the supervisor switches the detector bias.

COMMANDS (run from the folder containing this file, venv active):
    Fake run (no hardware, folder name gets FAKE_ automatically):
        python lynx_measure.py --label test
    Connection check of both Lynx boxes (ping + vendor connect, changes nothing):
        python lynx_measure.py --check
    Read-only look at what the real Lynx returns (no start, no HV):
        python lynx_measure.py --probe
    REAL measurement, both boxes, sync cable connected:
        python lynx_measure.py --real --sync --preset 1800 --label W1_pos1
    Real, hours:
        python lynx_measure.py --real --sync --hours 2 --label W1_pos1
    Re-analyse a saved run:
        python lynx_measure.py --from-csv runs\\<folder>\\events.csv --label replot
    Stop early: press Ctrl+C - the data collected so far is kept.
==============================================================================
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import re
import sys
import time
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

# =============================================================================
# CONFIG - the only block you normally edit
# =============================================================================
PROTON_NAME = "IBIS Prot"
PROTON_IP = "130.183.43.184"
ALPHA_NAME = "S Alpha"
ALPHA_IP = "130.183.43.185"
INPUT = 1                      # analog input used on each box

PRESET_S = 600.0               # default measuring time in seconds
POLL_S = 2.0                   # read the list buffers this often
COINC_WINDOW_NS = 200_000      # software window (+-). Tighten after you see the dt peak.
ENABLE_HV = False              # LEAVE False. Bias is switched by the supervisor.

LYNX_USER = "administrator"    # default of the vendor examples; lab may have changed it
LYNX_PASSWORD = "password"

DATA_ROOT = Path(__file__).resolve().parent / "runs"

# Written into run_meta.json. Fill from the accelerator log / notebook.
SAMPLE_LABEL = "sample"
SAMPLE_NOTE = ""
BEAM_PARTICLE = "FILL_ME"
BEAM_ENERGY_KEV = None
BEAM_CURRENT_NA = None

# Energy calibration  E_keV = slope * channel + offset   (None = not calibrated yet)
# Get it from the test run with the Am-241 source (see lynx_test_sample.py).
CAL = {
    "proton": (None, None),    # (slope keV/ch, offset keV)
    "alpha": (None, None),
}

# Multiplier that turns the Lynx time stamp into nanoseconds. 1.0 = already ns.
# Check with  --probe  (look at the printed timebase) before trusting real dt values.
TIMESTAMP_TO_NS = 1.0

# SDK numeric codes (same as in your earlier scripts).
INPUT_MODE = 60
PRESET_OPTIONS = 38
PRESET_REAL = 40
NETWORK_MACHINE_NAME = 81
INPUT_VOLTAGE_STATUS = 161
MODE_TLIST = 5
PRESET_REAL_TIME = 1
CMD_START, CMD_STOP, CMD_CLEAR = 3, 4, 5
# !!! UNVERIFIED against the SDK manual - confirm before trusting synced data !!!
INPUT_EXTERNAL_SYNC_STATUS = 263
INPUT_EXTERNAL_SYNC_MODE = 264
SYNC_MASTER, SYNC_SLAVE, SYNC_ENABLED = 1, 0, 1


# =============================================================================
# Data record
# =============================================================================
@dataclass
class Event:
    channel: int
    timestamp_ns: int
    input_id: int
    source: str       # "proton" or "alpha"
    lynx_ip: str


CSV_FIELDS = ["channel", "timestamp_ns", "input_id", "source", "lynx_ip"]


# =============================================================================
# Fake detector (default mode, no hardware)
# =============================================================================
class FakeDetector:
    def __init__(self, source: str, name: str, ip: str):
        self.source, self.name, self.ip = source, name, ip

    def connect(self) -> None:
        print(f"[fake] connect {self.source} ({self.name}, {self.ip})")

    def setup(self, sync_mode, sync_on, hv, preset_s) -> None:
        print(f"[fake] setup {self.source}  preset={preset_s}s  sync={sync_on}")

    def start(self) -> None:
        print(f"[fake] start {self.source}")

    def poll(self) -> list[Event]:
        out: list[Event] = []
        t0 = 1_000_000_000
        for i in range(300):
            ts = t0 + i * 1_000_000
            if self.source == "proton":
                out.append(Event(int(random.gauss(2750, 120)), ts, INPUT, "proton", self.ip))
            else:
                if random.random() < 0.7:
                    jitter = int(random.gauss(0, 3000))
                    out.append(Event(int(random.gauss(650, 30)), ts + 80_000 + jitter,
                                     INPUT, "alpha", self.ip))
                if random.random() < 0.15:
                    out.append(Event(random.randint(100, 900),
                                     t0 + random.randint(0, 300_000_000),
                                     INPUT, "alpha", self.ip))
        return out

    def stop(self) -> None:
        print(f"[fake] stop {self.source}")

    def close(self) -> None:
        pass


# =============================================================================
# Real detector (Lynx via the vendor DataTypes library)
# =============================================================================
def load_vendor_factory():
    here = Path(__file__).resolve().parent
    for cand in (here.parent / "DataTypes", here / "DataTypes",
                 Path(r"C:\Lynx\SDK\PythonExamples\DataTypes"), Path(r"C:\DataTypes")):
        if cand.is_dir():
            sys.path.insert(0, str(cand))
            print(f"[vendor] using DataTypes from {cand}")
            break
    else:
        raise SystemExit(
            "STOP: vendor DataTypes folder not found. Put it next to the repo folder "
            "(..\\DataTypes) or at C:\\DataTypes.")
    try:
        from DeviceFactory import DeviceFactory  # type: ignore
    except SyntaxError as exc:
        raise SystemExit(
            "STOP: the vendor DataTypes library is Python 2 and cannot be imported on "
            f"Python {sys.version.split()[0]}.\nIt must be ported to Python 3 (or the vendor "
            f"Python 2 tools used) before real mode can work.\nDetail: {exc}")
    except ImportError as exc:
        raise SystemExit(f"STOP: could not import DeviceFactory: {exc}")
    return DeviceFactory


def _first_attr(obj, names):
    for n in names:
        if hasattr(obj, n):
            v = getattr(obj, n)
            return v() if callable(v) else v
    return None


class RealDetector:
    def __init__(self, source: str, name: str, ip: str, factory):
        self.source, self.name, self.ip, self.factory = source, name, ip, factory
        self.dev = None
        self.seen: set = set()
        self.missing_ts = 0

    def connect(self) -> None:
        self.dev = self.factory.createInstance(self.factory.DeviceInterface.IDevice)
        self.dev.open("", self.ip)
        machine = self.dev.getParameter(NETWORK_MACHINE_NAME, 0)
        print(f"Connected {self.source}: {self.name}  {self.ip}  machine name = {machine}")

    def setup(self, sync_mode, sync_on, hv, preset_s) -> None:
        d = self.dev
        d.lock(LYNX_USER, LYNX_PASSWORD, INPUT)
        d.control(CMD_STOP, INPUT)
        d.setParameter(INPUT_MODE, MODE_TLIST, INPUT)
        d.setParameter(PRESET_OPTIONS, PRESET_REAL_TIME, INPUT)
        d.setParameter(PRESET_REAL, float(preset_s), INPUT)
        if sync_on:
            try:
                d.setParameter(INPUT_EXTERNAL_SYNC_MODE, sync_mode, INPUT)
                d.setParameter(INPUT_EXTERNAL_SYNC_STATUS, SYNC_ENABLED, INPUT)
            except Exception as exc:
                raise SystemExit(
                    f"STOP: could not set external sync on {self.name}: {exc}\n"
                    "Sync codes in this script are unverified - check the SDK manual.")
        d.control(CMD_CLEAR, INPUT)
        if hv:
            print(f"WARNING: HV requested on {self.name}")
            d.setParameter(INPUT_VOLTAGE_STATUS, True, INPUT)

    def start(self) -> None:
        self.dev.control(CMD_START, INPUT)

    def poll(self) -> list[Event]:
        raw = self.dev.getListData(INPUT)
        items = raw.getEvents() if hasattr(raw, "getEvents") else raw
        out: list[Event] = []
        for it in items:
            ch = _first_attr(it, ("channel", "Channel", "energy", "Energy"))
            if ch is None and isinstance(it, (int, float)):
                ch = it
            ts = _first_attr(it, ("timestamp_ns", "timestamp", "Timestamp", "time", "Time"))
            if ch is None:
                continue
            if ts is None:
                self.missing_ts += 1
                ts = 0
            ts = int(float(ts) * TIMESTAMP_TO_NS)
            key = (ts, int(ch))
            if key in self.seen:       # in case the buffer is returned again
                continue
            self.seen.add(key)
            out.append(Event(int(ch), ts, INPUT, self.source, self.ip))
        return out

    def stop(self) -> None:
        self.dev.control(CMD_STOP, INPUT)

    def close(self) -> None:
        try:
            self.dev.close()
        except Exception:
            pass


# =============================================================================
# Check (are both Lynx boxes reachable?  changes nothing on the instruments)
# =============================================================================
def check(ip_p: str, ip_a: str) -> int:
    import platform
    import subprocess
    boxes = (("proton", PROTON_NAME, ip_p), ("alpha", ALPHA_NAME, ip_a))
    count_flag = "-n" if platform.system() == "Windows" else "-c"
    print("STEP 1 - network (ping)")
    reachable = {}
    for source, name, ip in boxes:
        try:
            r = subprocess.run(["ping", count_flag, "2", ip], capture_output=True,
                               text=True, timeout=20)
            reachable[source] = "TTL=" in r.stdout.upper()
        except Exception as exc:
            print(f"  could not run ping: {exc}")
            reachable[source] = False
        print(f"  {name:10s} {ip:16s} {'OK - answers ping' if reachable[source] else 'FAIL - no answer'}")
    if not all(reachable.values()):
        print("\nRESULT: NOT READY. Fix the network first (cable, Power LED, network port, IP).")
        return 1
    print("\nSTEP 2 - Lynx software connection (vendor library)")
    factory = load_vendor_factory()
    good = 0
    for source, name, ip in boxes:
        try:
            det = RealDetector(source, name, ip, factory)
            det.connect()
            det.close()
            print(f"  {name:10s} OK - connected")
            good += 1
        except Exception as exc:
            print(f"  {name:10s} FAIL - {exc}")
    if good == 2:
        print("\nRESULT: READY. Both Lynx boxes answer. Next: --probe, then the test run.")
        return 0
    print("\nRESULT: NOT READY. A box answers ping but the connection failed (see messages above).")
    return 1


# =============================================================================
# Probe (read-only look at the real Lynx)
# =============================================================================
def probe() -> int:
    factory = load_vendor_factory()
    for source, name, ip in (("proton", PROTON_NAME, PROTON_IP), ("alpha", ALPHA_NAME, ALPHA_IP)):
        print("=" * 60)
        print(f"PROBE {source}: {name} {ip}")
        try:
            det = RealDetector(source, name, ip, factory)
            det.connect()
        except Exception as exc:
            print(f"  cannot connect: {exc}")
            continue
        try:
            raw = det.dev.getListData(INPUT)
            print("  getListData returned:", type(raw))
            print("  attributes:", [a for a in dir(raw) if not a.startswith("_")])
            for meth in ("getTimebase", "getLiveTime", "getRealTime", "getStartTime", "getFlags"):
                if hasattr(raw, meth):
                    try:
                        print(f"  {meth}() =", getattr(raw, meth)())
                    except Exception as exc:
                        print(f"  {meth}() failed: {exc}")
            evs = list(raw.getEvents())[:3] if hasattr(raw, "getEvents") else []
            print(f"  first events: {evs!r}")
            if evs:
                print("  event attributes:", [a for a in dir(evs[0]) if not a.startswith("_")])
        except Exception as exc:
            print(f"  getListData failed (maybe needs lock/start): {exc}")
        det.close()
    print("=" * 60)
    print("Check: do events have a channel AND a time stamp? What is the timebase?")
    print("If the time stamp is not in ns, set TIMESTAMP_TO_NS at the top of this file.")
    return 0


# =============================================================================
# Acquisition
# =============================================================================
def slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", text.strip()) or "run"


def append_csv(path: Path, events: list[Event]) -> None:
    new = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if new:
            w.writeheader()
        for e in events:
            w.writerow(asdict(e))


def acquire(det_p, det_a, preset_s: float, hv: bool, sync_on: bool, csv_path: Path):
    det_p.connect()
    det_a.connect()
    det_p.setup(SYNC_MASTER, sync_on, hv, preset_s)
    det_a.setup(SYNC_SLAVE, sync_on, hv, preset_s)
    fake = isinstance(det_p, FakeDetector)
    events: list[Event] = []
    t_start_p = t_start_a = time.monotonic()
    try:
        t_start_p = time.monotonic()
        det_p.start()
        t_start_a = time.monotonic()
        det_a.start()
        gap = t_start_a - t_start_p
        print(f"Started both. Host-time gap between the two start commands: {gap * 1000:.0f} ms")
        deadline = time.monotonic() + (0.0 if fake else float(preset_s))
        print("Fake mode: single read." if fake else
              f"Acquiring {preset_s:.0f} s, reading every {POLL_S} s.  Ctrl+C = stop early.")
        while True:
            chunk = det_p.poll() + det_a.poll()
            if chunk:
                events.extend(chunk)
                append_csv(csv_path, chunk)
            n_p = sum(1 for e in events if e.source == "proton")
            print(f"  total={len(events)}  proton={n_p}  alpha={len(events) - n_p}")
            if fake or time.monotonic() >= deadline:
                break
            time.sleep(POLL_S)
    except KeyboardInterrupt:
        print("\nCtrl+C: stopping early, keeping data so far.")
    finally:
        for d in (det_p, det_a):
            if not fake:
                try:
                    last = d.poll()
                    if last:
                        events.extend(last)
                        append_csv(csv_path, last)
                except Exception:
                    pass
            try:
                d.stop()
            except Exception as exc:
                print(f"WARNING: stop failed on {d.name}: {exc}")
            d.close()
    events.sort(key=lambda e: e.timestamp_ns)
    return events, (t_start_a - t_start_p)


def save_sorted_csv(path: Path, events: list[Event]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        w.writeheader()
        for e in events:
            w.writerow(asdict(e))
    print(f"[raw] wrote {path}  ({len(events)} rows)")


def load_csv(path: Path) -> list[Event]:
    out = []
    with path.open(newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            out.append(Event(int(r["channel"]), int(r["timestamp_ns"]), int(r["input_id"]),
                             r.get("source") or "unknown", r.get("lynx_ip") or ""))
    out.sort(key=lambda e: e.timestamp_ns)
    return out


# =============================================================================
# Analysis
# =============================================================================
def find_pairs(events: list[Event], window_ns: int):
    a = [e for e in events if e.source == "proton"]
    b = [e for e in events if e.source == "alpha"]
    pairs = []
    j = 0
    for ea in a:
        while j < len(b) and b[j].timestamp_ns < ea.timestamp_ns - window_ns:
            j += 1
        k = j
        while k < len(b) and b[k].timestamp_ns <= ea.timestamp_ns + window_ns:
            pairs.append((ea, b[k]))
            k += 1
    return pairs


def to_kev(source: str, ch: float):
    slope, off = CAL[source]
    if slope is None:
        return None
    return slope * ch + (off or 0.0)


def peak_channel(channels: list[int], bin_w: int = 4):
    if len(channels) < 20:
        return None
    c = Counter(ch // bin_w for ch in channels)
    top = max(c, key=c.get)
    near = [ch for ch in channels if abs(ch // bin_w - top) <= 3]
    return sum(near) / len(near)


def dt_peak(pairs):
    if not pairs:
        return None
    nb = 40
    width = 2.0 * COINC_WINDOW_NS / nb
    counts = [0] * nb
    for ea, eb in pairs:
        dt = eb.timestamp_ns - ea.timestamp_ns
        idx = int((dt + COINC_WINDOW_NS) / width)
        counts[min(max(idx, 0), nb - 1)] += 1
    top = max(range(nb), key=lambda i: counts[i])
    median = sorted(counts)[nb // 2]
    centre = -COINC_WINDOW_NS + (top + 0.5) * width
    return {"dt_peak_ns": centre, "peak_counts": counts[top],
            "median_counts": median, "peak_over_median": counts[top] / max(median, 1)}


def write_pairs_csv(path: Path, pairs) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ch_proton", "t_proton_ns", "ch_alpha", "t_alpha_ns", "dt_ns",
                    "E_proton_keV", "E_alpha_keV"])
        for ea, eb in pairs:
            ep, ealpha = to_kev("proton", ea.channel), to_kev("alpha", eb.channel)
            w.writerow([ea.channel, ea.timestamp_ns, eb.channel, eb.timestamp_ns,
                        eb.timestamp_ns - ea.timestamp_ns,
                        "" if ep is None else f"{ep:.2f}", "" if ealpha is None else f"{ealpha:.2f}"])
    print(f"[pairs] wrote {path}  ({len(pairs)} rows)")


def build_summary(events, pairs, preset_s, cal_kev, missing_ts: int, sync_on) -> str:
    p = [e for e in events if e.source == "proton"]
    a = [e for e in events if e.source == "alpha"]
    span_s = (events[-1].timestamp_ns - events[0].timestamp_ns) / 1e9 if len(events) > 1 else 0.0
    rate = (lambda n: f"   rate ~ {n / span_s:.2f} /s" if span_s > 0 else "")
    lines = ["SUMMARY", "=" * 50,
             f"events total      : {len(events)}   (data time span {span_s:.1f} s)",
             f"proton events     : {len(p)}{rate(len(p))}",
             f"alpha events      : {len(a)}{rate(len(a))}",
             f"coincidence window: +-{COINC_WINDOW_NS} ns",
             f"pairs             : {len(pairs)}"]
    d = dt_peak(pairs)
    if d:
        lines.append(f"dt peak           : {d['dt_peak_ns'] / 1000:.1f} us   "
                     f"(peak {d['peak_counts']} vs median bin {d['median_counts']}, "
                     f"ratio {d['peak_over_median']:.1f})")
        lines.append("  ratio >> 1 = real time correlation; ratio ~ 1 = flat = random pairs only")
    for src, evs in (("proton", p), ("alpha", a)):
        pk = peak_channel([e.channel for e in evs])
        if pk is not None:
            lines.append(f"{src} spectrum peak channel ~ {pk:.1f}")
    if cal_kev:
        pk = peak_channel([e.channel for e in a])
        if pk:
            lines.append(f"CALIBRATION HINT  : alpha line {cal_kev} keV at ch {pk:.1f}  ->  "
                         f"slope = {cal_kev / pk:.4f} keV/ch (offset assumed 0). "
                         f"Put it in CAL['alpha'] at the top of lynx_measure.py.")
        else:
            lines.append("CALIBRATION HINT  : not enough alpha events to find a peak.")
    warn = []
    if not events:
        warn.append("NO EVENTS: bias off? wrong input? detector not connected? cable?")
    if events and not p:
        warn.append("No proton events.")
    if events and not a:
        warn.append("No alpha events.")
    if missing_ts:
        warn.append(f"{missing_ts} events had NO time stamp - coincidences are invalid. Run --probe.")
    if sync_on is False:
        warn.append("Sync was NOT enabled: timestamps of the two boxes are not comparable.")
    if events and not pairs:
        warn.append("No pairs: window too small, no sync, or no true coincidences.")
    lines += [""] + [f"!!! {w}" for w in warn]
    return "\n".join(lines)


def make_plots(events, pairs, png: Path) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib not installed - skipping plots (pip install matplotlib)")
        return
    p = [e.channel for e in events if e.source == "proton"]
    a = [e.channel for e in events if e.source == "alpha"]
    fig, ax = plt.subplots(2, 2, figsize=(11, 8))
    if p:
        ax[0][0].hist(p, bins=100, color="tab:red")
    ax[0][0].set_title("Proton spectrum (channel)")
    ax[0][0].set_xlabel("channel")
    if a:
        ax[0][1].hist(a, bins=100, color="tab:blue")
    ax[0][1].set_title("Alpha spectrum (channel)")
    ax[0][1].set_xlabel("channel")
    dts = [(eb.timestamp_ns - ea.timestamp_ns) / 1000.0 for ea, eb in pairs]
    if dts:
        ax[1][0].hist(dts, bins=60, color="tab:green")
    ax[1][0].set_title("dt = t_alpha - t_proton")
    ax[1][0].set_xlabel("us")
    if pairs:
        ax[1][1].scatter([ea.channel for ea, _ in pairs][:20000],
                         [eb.channel for _, eb in pairs][:20000], s=6, alpha=0.4)
    ax[1][1].set_title("Coincident events")
    ax[1][1].set_xlabel("proton channel")
    ax[1][1].set_ylabel("alpha channel")
    fig.tight_layout()
    fig.savefig(png, dpi=130)
    plt.close(fig)
    print(f"[plot] wrote {png}")


# =============================================================================
# Main
# =============================================================================
def main(argv=None, profile=None) -> int:
    prof = {"label": SAMPLE_LABEL, "preset": PRESET_S, "note": SAMPLE_NOTE,
            "cal_kev": None, "test": False}
    if profile:
        prof.update(profile)

    ap = argparse.ArgumentParser(description="Lynx proton/alpha coincidence measurement")
    ap.add_argument("--real", action="store_true", help="use the real Lynx boxes (default: fake)")
    ap.add_argument("--check", action="store_true", help="ping + connect to both Lynx boxes, then exit")
    ap.add_argument("--probe", action="store_true", help="read-only look at the real Lynx, then exit")
    ap.add_argument("--sync", action="store_true", help="enable Ext Sync (cable must be connected)")
    ap.add_argument("--no-sync", action="store_true", help="real run WITHOUT sync (coincidences invalid)")
    ap.add_argument("--ip-proton", default=PROTON_IP)
    ap.add_argument("--ip-alpha", default=ALPHA_IP)
    ap.add_argument("--label", default=prof["label"])
    ap.add_argument("--preset", type=float, default=prof["preset"], help="seconds")
    ap.add_argument("--hours", type=float, default=0.0, help="overrides --preset")
    ap.add_argument("--cal-kev", type=float, default=prof["cal_kev"],
                    help="known alpha line in keV (Am-241: 5485.56) to print a calibration hint")
    ap.add_argument("--hv", action="store_true", help="request HV - lab/supervisor only")
    ap.add_argument("--from-csv", default="", help="re-analyse an existing events.csv")
    args = ap.parse_args(argv)

    if args.check:
        return check(args.ip_proton, args.ip_alpha)
    if args.probe:
        return probe()

    preset_s = args.hours * 3600.0 if args.hours > 0 else args.preset
    hv = ENABLE_HV or args.hv
    if hv:
        if input("HV requested. Supervisor present and bias settings confirmed? Type YES: ") != "YES":
            print("HV cancelled.")
            return 1

    if args.real and not (args.sync or args.no_sync) and not args.from_csv:
        print("STOP: real run needs --sync (cable connected) or --no-sync (coincidences invalid).")
        return 2
    sync_on = args.sync

    base = slug(args.label)
    if prof["test"] and not base.upper().startswith("TEST"):
        base = "TEST_" + base
    if args.from_csv:
        mode, label = "reanalysis", "REANALYSIS_" + base
    elif args.real:
        mode, label = "real", base
    else:
        mode, label = "fake", "FAKE_" + base

    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    run_dir = DATA_ROOT / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{label}"
    run_dir.mkdir(parents=True, exist_ok=False)
    print(f"[run] folder {run_dir}")
    print(f"[run] mode={mode}  preset={preset_s:.0f}s  sync={sync_on}  HV={'REQUESTED' if hv else 'off'}")
    if BEAM_PARTICLE == "FILL_ME" and mode == "real":
        print("[note] BEAM_* fields in the CONFIG block are still empty - write them in the notebook.")

    csv_path = run_dir / "events.csv"
    missing_ts, gap = 0, None
    if args.from_csv:
        events = load_csv(Path(args.from_csv))
        print(f"Loaded {len(events)} events from {args.from_csv}")
    else:
        if args.real:
            factory = load_vendor_factory()
            det_p = RealDetector("proton", PROTON_NAME, args.ip_proton, factory)
            det_a = RealDetector("alpha", ALPHA_NAME, args.ip_alpha, factory)
        else:
            det_p = FakeDetector("proton", PROTON_NAME, args.ip_proton)
            det_a = FakeDetector("alpha", ALPHA_NAME, args.ip_alpha)
        events, gap = acquire(det_p, det_a, preset_s, hv, sync_on, csv_path)
        missing_ts = getattr(det_p, "missing_ts", 0) + getattr(det_a, "missing_ts", 0)
    save_sorted_csv(csv_path, events)

    pairs = find_pairs(events, COINC_WINDOW_NS)
    write_pairs_csv(run_dir / "coinc_pairs.csv", pairs)
    summary = build_summary(events, pairs, preset_s, args.cal_kev, missing_ts,
                            sync_on if mode == "real" else None)
    (run_dir / "summary.txt").write_text(summary, encoding="utf-8")
    print("\n" + summary + "\n")
    make_plots(events, pairs, run_dir / "spectra.png")

    (run_dir / "run_meta.json").write_text(json.dumps({
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "test_run": prof["test"],
        "sample_label": args.label,
        "sample_note": prof["note"],
        "proton_lynx": {"name": PROTON_NAME, "ip": args.ip_proton, "input": INPUT},
        "alpha_lynx": {"name": ALPHA_NAME, "ip": args.ip_alpha, "input": INPUT},
        "sync_enabled": sync_on,
        "start_gap_host_s": gap,
        "preset_s": preset_s,
        "poll_s": POLL_S,
        "coinc_window_ns": COINC_WINDOW_NS,
        "timestamp_to_ns": TIMESTAMP_TO_NS,
        "hv_requested": hv,
        "beam_particle": BEAM_PARTICLE,
        "beam_energy_keV": BEAM_ENERGY_KEV,
        "beam_current_nA": BEAM_CURRENT_NA,
        "calibration": {k: {"slope_keV_per_ch": v[0], "offset_keV": v[1]} for k, v in CAL.items()},
        "n_events": len(events),
        "n_pairs": len(pairs),
        "events_without_timestamp": missing_ts,
        "python": sys.version.split()[0],
    }, indent=2), encoding="utf-8")
    print(f"[run] all files in {run_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

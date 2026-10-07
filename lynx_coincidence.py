#!/usr/bin/env python3
"""
==============================================================================
LYNX LIST-MODE SCRIPT  —  coincidence data for deuterium depth profiling
==============================================================================

WHERE THIS FILE LIVES
    Copy / keep it here on the lab PC:
        C:\\Lynx\\SDK\\PythonExamples\\Examples\\lynx_coincidence.py

    Official vendor library (do not edit) sits next door:
        C:\\Lynx\\SDK\\PythonExamples\\DataTypes\\

WHAT THIS FILE DOES BY DEFAULT (raw data only)
    1. Talks to a FAKE Lynx so you can run it at home.
    2. Writes one CSV of raw list-mode events.
    3. Prints how many events and coincidence pairs it found.
    4. Does NOT enable high voltage.
    5. Does NOT show graphs or fits until you uncomment those blocks.

HOW TO RUN
    cd C:\\Lynx\\SDK\\PythonExamples\\Examples
    python lynx_coincidence.py

    Later, real box (after DataTypes works on Python 3):
    python lynx_coincidence.py --real --ip 192.168.1.50

WHERE YOU PUT NUMBERS
    Edit the CONFIG section below (IP, inputs, times, file names).

WHERE OUTPUT GOES
    Same folder as this script, unless you change OUT_CSV:
        listmode_events.csv     always (raw data)
        listmode_events.png     only if you uncomment PLOT BASIC
        coinc_pairs.csv         only if you uncomment SAVE PAIRS
        listmode_fits.png       only if you uncomment FITS
        listmode_depth.png      only if you uncomment DEPTH

UNCOMMENT LATER
    Search this file for:    UNCOMMENT TO ENABLE
    Delete the leading "# " on those lines (keep the indent).
==============================================================================
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


# =============================================================================
# SECTION 1 — CONFIG  (this is where YOU put lab data)
# =============================================================================
# Lynx Ethernet address from the lab (not the serial number on the chassis).
LYNX_IP = "192.168.1.50"

# Which analog inputs on the Lynx. If you have only ONE detector, set both to 1.
INPUT_A = 1
INPUT_B = 2

# How long a REAL acquisition lasts, in seconds. Override with --hours or --preset.
# Hours are OK if we poll list buffers (see POLL_S). Fake mode does not wait.
PRESET_S = 2.0

# Empty the Lynx list buffer this often (seconds) so it does not overflow on long runs.
POLL_S = 2.0

# Software coincidence window. Pair A with B if |tB - tA| is smaller than this.
# Start wide. After you see the dt peak, tighten it.
# NRA p–α coincidence at IPP is often tens of ns to a few µs after delay cables;
# 200 µs is a safe first software window, not a physics claim.
COINC_WINDOW_NS = 200_000

# Never turn this on until a supervisor confirms HV polarity and voltage.
ENABLE_HV = False

# Parent folder for all runs. Each start makes a new subfolder inside this.
# On the Omen this can be e.g. r"C:\Lynx\data"
DATA_ROOT = Path(__file__).resolve().parent / "runs"

# Labels written into the folder name and run_meta.json — edit per sample.
SAMPLE_LABEL = "W_tile"
SAMPLE_NOTE = "tungsten tile, D-loaded PFC-style sample"
BEAM_PARTICLE = "4He"          # confirm at IPP: D profiling is often 3He NRA, not 4He
BEAM_ENERGY_KEV = 2000         # GUESS only — typical tandem He range 500–4500 keV
BEAM_CURRENT_NA = 20           # GUESS only — ask the operator
LAB_NAME = "IPP Garching (confirm)"
DETECTOR_NOTE = "Si / PIPS on Lynx input(s); serial still unknown"

# File names inside each run folder.
OUT_CSV = "events.csv"
OUT_PAIRS_CSV = "coinc_pairs.csv"
OUT_PNG = "spectra.png"
OUT_META = "run_meta.json"

# Linear energy calibration:  E_keV = CAL_SLOPE * channel + CAL_OFFSET
# Fill after an alpha-source calibration (Am-241 5486 keV is the usual lab line).
CAL_SLOPE = 2.0     # GUESS keV/ch until you fit a source
CAL_OFFSET = 0.0    # keV

# Default Lynx login used by the official examples. Lab may have changed it.
LYNX_USER = "administrator"
LYNX_PASSWORD = "password"


# =============================================================================
# SECTION 2 — SDK NUMERIC CODES  (from the Canberra/Mirion CD, do not invent)
# =============================================================================
INPUT_MODE = 60
PRESET_OPTIONS = 38
PRESET_REAL = 40
NETWORK_MACHINE_NAME = 81
INPUT_VOLTAGE_STATUS = 161
MODE_TLIST = 5          # time-stamped list mode (needed for software coincidence)
PRESET_REAL_TIME = 1
CMD_START = 3
CMD_STOP = 4
CMD_CLEAR = 5


# =============================================================================
# SECTION 3 — EVENT RECORD  (one row of raw data)
# =============================================================================
@dataclass
class Event:
    channel: int        # ADC channel ~ energy, before calibration
    timestamp_ns: int   # time stamp in nanoseconds
    input_id: int       # 1 or 2 = which Lynx input / detector


# =============================================================================
# SECTION 4 — FAKE LYNX  (lets you run with no hardware)
# =============================================================================
class FakeLynx:
    """Same method names as the real Device class in DataTypes."""

    def open(self, client, device):
        print(f"[fake] open  device={device}")

    def lock(self, user, password, inp):
        print(f"[fake] lock  user={user!r}  input={inp}")

    def setParameter(self, code, value, inp):
        pass

    def getParameter(self, code, inp=0):
        if code == NETWORK_MACHINE_NAME:
            return "FAKE-LYNX"
        return 0

    def control(self, cmd, inp):
        names = {3: "Start", 4: "Stop", 5: "Clear"}
        print(f"[fake] {names.get(cmd, cmd)}  input={inp}")

    def getListData(self, inp):
        # Fake two detectors: many B hits follow A by ~80 us (true coincidences).
        t0 = time.time_ns()
        out: list[Event] = []
        for i in range(50):
            ts = t0 + i * 1_000_000
            out.append(Event(random.randint(800, 1600), ts, INPUT_A))
            if random.random() < 0.7:
                out.append(Event(random.randint(800, 1600), ts + 80_000, INPUT_B))
            if random.random() < 0.15:
                out.append(Event(random.randint(200, 400), ts + 5_000_000, INPUT_B))
        return out

    def close(self):
        pass


# =============================================================================
# SECTION 5 — REAL LYNX  (only after DataTypes is ported to Python 3)
# =============================================================================
def open_real_lynx(ip: str):
    here = Path(__file__).resolve().parent
    sys.path.insert(0, str(here.parent / "DataTypes"))
    from DeviceFactory import DeviceFactory  # type: ignore

    lynx = DeviceFactory.createInstance(DeviceFactory.DeviceInterface.IDevice)
    lynx.open("", ip)
    return lynx


class RealListAdapter:
    def __init__(self, device):
        self.d = device

    def open(self, client, device):
        pass

    def lock(self, user, password, inp):
        self.d.lock(user, password, inp)

    def setParameter(self, code, value, inp):
        self.d.setParameter(code, value, inp)

    def getParameter(self, code, inp=0):
        return self.d.getParameter(code, inp)

    def control(self, cmd, inp):
        self.d.control(cmd, inp)

    def getListData(self, inp):
        raw = self.d.getListData(inp)
        events: list[Event] = []
        for item in raw.getEvents():
            if hasattr(item, "channel"):
                events.append(Event(int(item.channel), int(item.timestamp_ns), inp))
            else:
                events.append(Event(int(item), 0, inp))
        return events

    def close(self):
        try:
            self.d.close()
        except Exception:
            pass


# =============================================================================
# SECTION 6 — RUN FOLDER + ACQUISITION
# Each start creates:  DATA_ROOT / YYYYMMDD_HHMMSS_SAMPLE_LABEL /
# =============================================================================
def slug(text: str) -> str:
    keep = []
    for ch in text.strip().replace(" ", "_"):
        if ch.isalnum() or ch in "-_":
            keep.append(ch)
    return "".join(keep) or "run"


def make_run_dir(label: str) -> Path:
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    folder = DATA_ROOT / f"{stamp}_{slug(label)}"
    folder.mkdir(parents=True, exist_ok=False)
    print(f"[run] folder {folder}")
    return folder


def write_meta(path: Path, extra: dict) -> None:
    payload = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "sample_label": SAMPLE_LABEL,
        "sample_note": SAMPLE_NOTE,
        "beam_particle": BEAM_PARTICLE,
        "beam_energy_keV_GUESS": BEAM_ENERGY_KEV,
        "beam_current_nA_GUESS": BEAM_CURRENT_NA,
        "lab": LAB_NAME,
        "detector_note": DETECTOR_NOTE,
        "lynx_ip": extra.get("ip"),
        "mode": extra.get("mode"),
        "preset_s": extra.get("preset_s"),
        "poll_s": POLL_S,
        "coinc_window_ns": COINC_WINDOW_NS,
        "input_A": INPUT_A,
        "input_B": INPUT_B,
        "hv_enabled": extra.get("hv"),
        "cal_slope_keV_per_ch": CAL_SLOPE,
        "cal_offset_keV": CAL_OFFSET,
        "python": sys.version.split()[0],
        "n_events": extra.get("n_events"),
        "n_pairs": extra.get("n_pairs"),
        "notes": (
            "Beam energy/current are GUESSES until the accelerator log is copied. "
            "D depth profiling in W at IPP Garching is usually D(3He,p)4He NRA "
            "with 0.5–4.5 MeV 3He, not a 4He analysis beam. Confirm on the beamline."
        ),
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"[run] wrote {path}")


def append_events_csv(path: Path, events: list[Event], write_header: bool) -> None:
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["channel", "timestamp_ns", "input_id"])
        if write_header:
            w.writeheader()
        for e in events:
            w.writerow(asdict(e))


def acquire(lynx, ip: str, enable_hv: bool, preset_s: float, csv_path: Path | None = None) -> list[Event]:
    """
    Start list mode and PULL buffers every POLL_S seconds.
    One long sleep of several hours would overflow the Lynx list memory.
    """
    inputs = {INPUT_A, INPUT_B}
    lynx.open("", ip)
    name = lynx.getParameter(NETWORK_MACHINE_NAME, 0)
    print(f"Connected to: {name}")

    for inp in sorted(inputs):
        lynx.lock(LYNX_USER, LYNX_PASSWORD, inp)
        lynx.control(CMD_STOP, inp)
        lynx.setParameter(INPUT_MODE, MODE_TLIST, inp)
        lynx.setParameter(PRESET_OPTIONS, PRESET_REAL_TIME, inp)
        lynx.setParameter(PRESET_REAL, float(preset_s), inp)
        lynx.control(CMD_CLEAR, inp)
        if enable_hv:
            print("WARNING: requesting HV on input", inp)
            lynx.setParameter(INPUT_VOLTAGE_STATUS, True, inp)

    for inp in sorted(inputs):
        lynx.control(CMD_START, inp)

    events: list[Event] = []
    header_needed = True
    fake = isinstance(lynx, FakeLynx)
    deadline = time.monotonic() + (0.0 if fake else float(preset_s))
    print("Fake mode: single buffer" if fake else f"Acquiring {preset_s} s, poll every {POLL_S} s")

    while True:
        chunk: list[Event] = []
        for inp in sorted(inputs):
            chunk.extend(lynx.getListData(inp))
        if chunk:
            events.extend(chunk)
            if csv_path is not None:
                append_events_csv(csv_path, chunk, header_needed)
                header_needed = False
            print(f"  +{len(chunk)} events  total={len(events)}")
        if fake or time.monotonic() >= deadline:
            break
        time.sleep(POLL_S)

    for inp in sorted(inputs):
        extra = lynx.getListData(inp)
        if extra:
            events.extend(extra)
            if csv_path is not None:
                append_events_csv(csv_path, extra, header_needed)
                header_needed = False
        lynx.control(CMD_STOP, inp)

    events.sort(key=lambda e: e.timestamp_ns)
    print(f"Raw events read: {len(events)}")
    return events


def save_raw_csv(path: Path, events: list[Event]) -> None:
    """Always-on raw dump. This is the file you keep for the thesis."""
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["channel", "timestamp_ns", "input_id"])
        w.writeheader()
        for e in events:
            w.writerow(asdict(e))
    print(f"[raw] wrote {path.resolve()}   ({len(events)} rows)")


def load_raw_csv(path: Path) -> list[Event]:
    """If you already have a CSV and only want analysis, use --from-csv."""
    events: list[Event] = []
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            events.append(
                Event(int(row["channel"]), int(row["timestamp_ns"]), int(row["input_id"]))
            )
    return events


# =============================================================================
# SECTION 7 — COINCIDENCE  (uses raw timestamps, still no fit)
# OUTPUT: list of (event_A, event_B) pairs
# =============================================================================
def coincidences(events: list[Event], window_ns: int) -> list[tuple[Event, Event]]:
    a = sorted((e for e in events if e.input_id == INPUT_A), key=lambda e: e.timestamp_ns)
    b = sorted((e for e in events if e.input_id == INPUT_B), key=lambda e: e.timestamp_ns)
    pairs: list[tuple[Event, Event]] = []
    j = 0
    for ea in a:
        while j < len(b) and b[j].timestamp_ns < ea.timestamp_ns - window_ns:
            j += 1
        k = j
        while k < len(b) and b[k].timestamp_ns <= ea.timestamp_ns + window_ns:
            pairs.append((ea, b[k]))
            k += 1
    return pairs


def save_pairs_csv(path: Path, pairs: list[tuple[Event, Event]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ch_A", "t_A_ns", "ch_B", "t_B_ns", "dt_ns"])
        for ea, eb in pairs:
            w.writerow(
                [
                    ea.channel,
                    ea.timestamp_ns,
                    eb.channel,
                    eb.timestamp_ns,
                    eb.timestamp_ns - ea.timestamp_ns,
                ]
            )
    print(f"[pairs] wrote {path.resolve()}   ({len(pairs)} rows)")


# =============================================================================
# SECTION 8 — OPTIONAL MATH  (used only if you uncomment fits)
# =============================================================================
def gaussian(x, amp, mu, sigma):
    if sigma == 0:
        return 0.0
    return amp * math.exp(-0.5 * ((x - mu) / sigma) ** 2)


def fit_gaussian(values: list[float]) -> tuple[float, float, float] | None:
    """
    Tiny 3-parameter Gaussian from histogram moments.
    Replace with scipy.optimize.curve_fit later if you want a proper fit.
    Returns (amplitude, mean, sigma) or None.
    """
    if len(values) < 8:
        return None
    mu = sum(values) / len(values)
    var = sum((v - mu) ** 2 for v in values) / len(values)
    sigma = math.sqrt(var) if var > 0 else 1.0
    amp = float(len(values))
    return amp, mu, sigma


def channel_to_keV(channel: float) -> float:
    return CAL_SLOPE * channel + CAL_OFFSET


# =============================================================================
# SECTION 9 — OPTIONAL PLOTS AND FITS
# These functions exist so uncommenting ONE line in main() is enough.
# =============================================================================
def plot_basic(events: list[Event], pairs: list[tuple[Event, Event]], png: Path) -> None:
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib missing. Install:  py -m pip install matplotlib")
        return

    ch_a = [e.channel for e in events if e.input_id == INPUT_A]
    ch_b = [e.channel for e in events if e.input_id == INPUT_B]
    dts_us = [(eb.timestamp_ns - ea.timestamp_ns) / 1000.0 for ea, eb in pairs]

    fig, axes = plt.subplots(1, 3, figsize=(12, 3.6))
    axes[0].hist(ch_a, bins=40, alpha=0.7, label="input A")
    if ch_b:
        axes[0].hist(ch_b, bins=40, alpha=0.7, label="input B")
    axes[0].set_xlabel("ADC channel")
    axes[0].set_ylabel("counts")
    axes[0].set_title("Raw spectra")
    axes[0].legend()

    if dts_us:
        axes[1].hist(dts_us, bins=40, color="tab:green")
    axes[1].axvline(0.0, color="k", linewidth=0.8)
    axes[1].set_xlabel("tB - tA (us)")
    axes[1].set_title("Coincidence dt")

    if pairs:
        axes[2].scatter(
            [ea.channel for ea, _ in pairs],
            [eb.channel for _, eb in pairs],
            s=12,
            alpha=0.6,
        )
    axes[2].set_xlabel("A channel")
    axes[2].set_ylabel("B channel")
    axes[2].set_title("Coincident channels")

    fig.tight_layout()
    fig.savefig(png, dpi=140)
    print(f"[plot] wrote {png.resolve()}")
    plt.show()


def plot_with_fits(events: list[Event], pairs: list[tuple[Event, Event]], png: Path) -> None:
    try:
        import matplotlib.pyplot as plt
        import numpy as np
    except ImportError:
        print("Need matplotlib and numpy for fits.")
        print("    py -m pip install matplotlib numpy")
        return

    ch_a = [float(e.channel) for e in events if e.input_id == INPUT_A]
    dts_us = [(eb.timestamp_ns - ea.timestamp_ns) / 1000.0 for ea, eb in pairs]
    fit_ch = fit_gaussian(ch_a)
    fit_dt = fit_gaussian(dts_us)

    fig, axes = plt.subplots(1, 2, figsize=(10, 3.6))
    if ch_a:
        n, bins, _ = axes[0].hist(ch_a, bins=40, alpha=0.75, label="A raw")
        if fit_ch:
            amp, mu, sig = fit_ch
            xs = np.linspace(min(ch_a), max(ch_a), 200)
            scale = max(n) if len(n) else 1.0
            ys = [gaussian(x, scale, mu, sig) for x in xs]
            axes[0].plot(xs, ys, color="tab:red", label=f"mu={mu:.1f} sig={sig:.1f}")
            print(f"[fit] A spectrum  mean={mu:.2f} ch   sigma={sig:.2f} ch")
    axes[0].set_xlabel("ADC channel")
    axes[0].set_title("Spectrum + Gaussian")
    axes[0].legend()

    if dts_us:
        n, bins, _ = axes[1].hist(dts_us, bins=40, color="tab:green", alpha=0.75)
        if fit_dt:
            amp, mu, sig = fit_dt
            xs = np.linspace(min(dts_us), max(dts_us), 200)
            scale = max(n) if len(n) else 1.0
            ys = [gaussian(x, scale, mu, sig) for x in xs]
            axes[1].plot(xs, ys, color="tab:red", label=f"mu={mu:.1f} us sig={sig:.1f}")
            print(f"[fit] coinc dt    mean={mu:.2f} us  sigma={sig:.2f} us")
    axes[1].set_xlabel("tB - tA (us)")
    axes[1].set_title("dt + Gaussian")
    axes[1].legend()

    fig.tight_layout()
    fig.savefig(png, dpi=140)
    print(f"[plot-fit] wrote {png.resolve()}")
    plt.show()


def plot_energy_and_depth(pairs: list[tuple[Event, Event]], png: Path) -> None:
    """
    After you set CAL_SLOPE and CAL_OFFSET from an alpha source.
    Depth here is a PLACEHOLDER histogram of energy — replace with
    a stopping-power / kinematics table for the real D profile.
    """
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib missing.")
        return

    e_a = [channel_to_keV(ea.channel) for ea, _ in pairs]
    e_b = [channel_to_keV(eb.channel) for _, eb in pairs]

    fig, axes = plt.subplots(1, 2, figsize=(10, 3.6))
    axes[0].hist(e_a, bins=40, alpha=0.7, label="A")
    axes[0].hist(e_b, bins=40, alpha=0.7, label="B")
    axes[0].set_xlabel("Energy (keV)  [uses CAL_SLOPE / CAL_OFFSET]")
    axes[0].set_title("Calibrated coincident energies")
    axes[0].legend()

    axes[1].hist(e_a, bins=40, color="tab:purple")
    axes[1].set_xlabel("E_A (keV)  — stand-in for depth")
    axes[1].set_title("Placeholder depth-like histogram")

    fig.tight_layout()
    fig.savefig(png, dpi=140)
    print(f"[plot-depth] wrote {png.resolve()}")
    print("[plot-depth] CAL_SLOPE=", CAL_SLOPE, " CAL_OFFSET=", CAL_OFFSET)
    plt.show()


# =============================================================================
# SECTION 10 — MAIN  (order of work; uncomment extras at the bottom)
# =============================================================================
def main() -> int:
    parser = argparse.ArgumentParser(description="Lynx list-mode raw dump + optional analysis")
    parser.add_argument("--real", action="store_true", help="use real Lynx at --ip")
    parser.add_argument(
        "--ip",
        default="",
        help="Lynx IP. If omitted with --real, Command Prompt will ask.",
    )
    parser.add_argument("--hv", action="store_true", help="enable HV — lab only")
    parser.add_argument("--out", default="", help="unused if run folders are on; kept for old scripts")
    parser.add_argument("--label", default=SAMPLE_LABEL, help="sample name in the run folder")
    parser.add_argument("--preset", type=float, default=PRESET_S, help="count time in seconds")
    parser.add_argument("--hours", type=float, default=0.0, help="count time in hours (overrides --preset)")
    parser.add_argument(
        "--from-csv",
        default="",
        help="skip acquisition; analyse an existing raw CSV instead",
    )
    args = parser.parse_args()

    enable_hv = ENABLE_HV or args.hv
    preset_s = args.hours * 3600.0 if args.hours > 0 else float(args.preset)
    print("HV requested:" if enable_hv else "HV off")
    print(f"Preset: {preset_s} s  ({preset_s / 3600.0:.3f} h)")

    ip = args.ip.strip() or LYNX_IP
    if args.real and not args.ip.strip():
        typed = input(f"Lynx IP address [{LYNX_IP}]: ").strip()
        if typed:
            ip = typed
    print(f"Using IP: {ip}")

    run_dir = make_run_dir(args.label)
    raw_path = run_dir / OUT_CSV

    # ----- raw data in -----
    if args.from_csv:
        events = load_raw_csv(Path(args.from_csv))
        print(f"Loaded {len(events)} events from {args.from_csv}")
        mode = "from-csv"
    elif args.real:
        lynx = RealListAdapter(open_real_lynx(ip))
        try:
            events = acquire(lynx, ip, enable_hv, preset_s, raw_path)
        finally:
            lynx.close()
        mode = "real"
    else:
        lynx = FakeLynx()
        try:
            events = acquire(lynx, ip, enable_hv, preset_s, raw_path)
        finally:
            lynx.close()
        mode = "fake"

    # rewrite a clean sorted raw file at the end
    save_raw_csv(raw_path, events)

    n_a = sum(1 for e in events if e.input_id == INPUT_A)
    n_b = sum(1 for e in events if e.input_id == INPUT_B)
    print(f"Counts   input A={n_a}   input B={n_b}")

    pairs = coincidences(events, COINC_WINDOW_NS)
    print(f"Software coincidence pairs  |dt| < {COINC_WINDOW_NS} ns : {len(pairs)}")
    for ea, eb in pairs[:5]:
        print(
            f"    A ch={ea.channel}  B ch={eb.channel}  "
            f"dt={eb.timestamp_ns - ea.timestamp_ns} ns"
        )

    write_meta(
        run_dir / OUT_META,
        {
            "ip": ip,
            "mode": mode,
            "preset_s": preset_s,
            "hv": enable_hv,
            "n_events": len(events),
            "n_pairs": len(pairs),
        },
    )
    print(f"[run] all files in {run_dir}")

    # =========================================================================
    # OPTIONAL BLOCKS — delete the "# " at the start of a line to turn it on.
    # Search for: UNCOMMENT TO ENABLE
    # =========================================================================

    # --- UNCOMMENT TO ENABLE: save coincidence pairs as their own CSV ---
    # save_pairs_csv(run_dir / OUT_PAIRS_CSV, pairs)

    # --- UNCOMMENT TO ENABLE: three basic graphs (needs matplotlib) ---
    # plot_basic(events, pairs, run_dir / OUT_PNG)

    # --- UNCOMMENT TO ENABLE: same idea + simple Gaussian fits ---
    # plot_with_fits(events, pairs, run_dir / "fits.png")

    # --- UNCOMMENT TO ENABLE: energy-calibrated / placeholder depth plots ---
    # Set CAL_SLOPE and CAL_OFFSET in SECTION 1 first.
    # plot_energy_and_depth(pairs, run_dir / "depth.png")

    # Re-analyse an old file without the Lynx:
    #   python lynx_coincidence.py --from-csv listmode_events.csv

    print("Done. Raw file is the CSV. Uncomment plot/fit lines in SECTION 10 when ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

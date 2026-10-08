Lynx Python SDK Coincidence & Data Acquisition Suite — Complete Documentation Bundle

This document contains everything you need to know about setting up, running, and managing the Python script suite for the Mirion/Canberra Lynx digital signal analyzers. It compiles project instructions, script behaviors, setup steps, and execution guidelines into a single comprehensive reference.

---

1. Overview of the Scripts

hello_lynx.py
* Purpose: Introductory script to test basic communication and connectivity with a single Lynx device or its software emulator.
* Key Behavior: Defaults to an emulation mode (FakeLynx) to let you test code pipelines without hardware or high voltage. Supports real network communication via --real and --ip flags, though official vendor libraries (DataTypes) may require Python 2 compatibility handling or porting.
* Safety: Does not touch or enable high voltage.

lynx_coincidence.py
* Purpose: Comprehensive script for list-mode data acquisition, run folder organization, periodic buffer polling, and optional coincidence analysis.
* Key Behavior: Features an explicit configuration section (SECTION 1) for defining IPs, inputs, preset times, and calibration values. Automatically creates a timestamped run subfolder inside a runs/ directory complete with a run_meta.json metadata record. Polls list buffers periodically (POLL_S) to prevent memory overflow on long runs. Includes optional, commented-out blocks for Gaussian fits, energy conversion, and matplotlib visualization.

lynx_one_box.py
* Purpose: Tailored for experimental configurations where two silicon detectors (e.g., proton and alpha) are connected to a single multi-input Lynx box (Input 1 and Input 2).
* Key Behavior: Automatically assigns Input 1 as the proton detector and Input 2 as the alpha detector, acquires data in time-stamped list mode (MODE_TLIST), tags each event with its corresponding source role, and chronologically merges the event stream for coincidence analysis.

lynx_two_boxes.py
* Purpose: Built for advanced setups utilizing two separate physical Lynx boxes—one for protons (--ip-a) and one for alpha particles (--ip-b).
* Key Behavior: Requires a physical synchronization cable plugged into the External Sync BNCs and the --sync flag enabled. Configures one Lynx as the master (SYNC_MASTER) and the other as the slave (SYNC_SLAVE) to align their internal clocks, polls both simultaneously, tags data by source IP, and saves merged outputs.

---

2. Requirements & Environment Setup

Software Requirements
* Python: Python 3.10 or newer (Python 3.13 tested).
* Virtual Environment Setup (Recommended):
  cd C:\lynx-coincidence
  py -3.13 -m venv .venv
  .venv\Scripts\activate.bat
* Optional Visualization Packages:
  pip install matplotlib numpy

Vendor SDK Prerequisites
* To talk to real hardware, the official Canberra/Mirion SDK components (DataTypes, DeviceFactory, ParameterCodes) must be available in the parent directory structure (e.g., C:\Lynx\SDK\PythonExamples\DataTypes).

---

3. Quick Start & Execution Reference

Emulation Mode (No Hardware Required)
All scripts default to a built-in software emulator (FakeLynx), letting you verify data pipelines and test logic offline:
python hello_lynx.py
python lynx_coincidence.py --label W_tile_test
python lynx_one_box.py --label W_tile_test
python lynx_two_boxes.py --label W_tile_test

Real Hardware Mode
When connected to the lab network, execute scripts pointing to your specific device IPs (ensure high voltage safety protocols are observed before changing any HV flags):

* Single Lynx Basic Test:
  python hello_lynx.py --real --ip 192.168.1.50
* Coincidence Acquisition with Custom Preset (e.g., 10 seconds):
  python lynx_coincidence.py --real --ip 192.168.1.50 --preset 10.0 --label tungsten_sample
* Single Box / Dual Detectors:
  python lynx_one_box.py --real --ip 130.183.43.184 --label IBIS_Prot
* Dual Box / Synchronized Setup (Requires physical sync cable):
  python lynx_two_boxes.py --real --ip-a 130.183.43.184 --ip-b 130.183.43.185 --sync --label p_alpha

---

4. Output Data Architecture

When running any active acquisition script, output files are neatly organized inside a timestamped directory under runs/:
* events.csv: Raw, time-sorted stream of list-mode events containing channels, timestamps, and input identifications.
* coinc_pairs.csv (optional): Extracted time-correlated event pairs matching the coincidence window criteria.
* run_meta.json: Complete record of run metadata including IP addresses, acquisition mode, duration presets, sample descriptions, and system versioning.

---

5. Critical Safety Note
* High Voltage (HV): High voltage control parameters are disabled by default (ENABLE_HV = False) across all scripts. Never enable high voltage via script arguments (--hv) or code adjustments until a supervisor or beamline operator explicitly confirms hardware polarity and safe operating voltage limits.

#!/usr/bin/env python3
r"""CW_CONTINUOUS_V1: paired UTC receipt logging, raw preservation, 10-day default.
Run in PowerShell: .\.venv\Scripts\python.exe .\record_study_windows.py
Only for the supplied CONTINUOUS sketches; never use the older 60-second firmware.
"""
import argparse
import csv
import ctypes
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import sys
import time
from datetime import datetime, timezone
from uuid import uuid4

TITLE = b"# CW_CONTINUOUS_V1"
ROLES = {"MASTER_SINGLES", "SLAVE_COINCIDENCE"}
EVENT_FIELDS = ["received_utc", "host_monotonic_ns", "port", "segment", "role",
                "detector_name", "event_id", "device_uptime_us", "adc_trigger", "adc_sample",
                "local_triggers", "busy_us_before_event", "temp_adc", "missing_events_before"]
STATUS_FIELDS = ["received_utc", "host_monotonic_ns", "port", "segment", "role",
                 "device_uptime_us", "accepted_events", "local_triggers", "busy_us", "awaiting_rearm"]
HEALTH_FIELDS = ["received_utc", "host_monotonic_ns", "both_links_healthy",
                 "port_1", "role_1", "age_1_s", "port_2", "role_2", "age_2_s"]
RAW_FIELDS = ["received_utc", "host_monotonic_ns", "port", "offset", "bytes"]


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


class Storage:
    """Append-only daily files, raw bytes first. Flush each second, fsync every 10 s."""
    def __init__(self, root):
        self.root = Path(root)
        self.handles = {}
        self.writers = {}
        self.day = None
        self.last_flush = time.monotonic()
        self.last_sync = self.last_flush
        self.log = (self.root / "operations.jsonl").open("a", encoding="utf-8")

    def rotate(self, utc):
        day = utc[:10]
        if day != self.day:
            self.close_daily()
            self.day = day

    def csv(self, name, fields, row, utc):
        self.rotate(utc)
        key = name + ".csv"
        if key not in self.handles:
            path = self.root / f"{self.day}_{key}"
            new = not path.exists() or path.stat().st_size == 0
            handle = path.open("a", newline="", encoding="utf-8")
            self.handles[key] = handle
            self.writers[key] = csv.writer(handle)
            if new:
                self.writers[key].writerow(fields)
        self.writers[key].writerow(row)

    def raw(self, port, data, utc, mono_ns):
        self.rotate(utc)
        key = f"{port}_raw.bin"
        if key not in self.handles:
            self.handles[key] = (self.root / f"{self.day}_{key}").open("ab")
        handle = self.handles[key]
        offset = handle.tell()
        handle.write(data)
        self.csv("raw_chunks", RAW_FIELDS, [utc, mono_ns, port, offset, len(data)], utc)

    def operation(self, kind, **details):
        record = {"recorded_utc": utc_now(), "host_monotonic_ns": time.monotonic_ns(),
                  "kind": kind, **details}
        self.log.write(json.dumps(record, ensure_ascii=True) + "\n")
        self.log.flush()
        print(f"[{record['recorded_utc']}] {kind}: {json.dumps(details)}", flush=True)

    def flush(self, force=False):
        now = time.monotonic()
        if not force and now - self.last_flush < 1:
            return
        sync = force or now - self.last_sync >= 10
        for handle in [self.log, *self.handles.values()]:
            handle.flush()
            if sync:
                os.fsync(handle.fileno())
        self.last_flush = now
        if sync:
            self.last_sync = now

    def close_daily(self):
        for handle in self.handles.values():
            handle.flush()
            os.fsync(handle.fileno())
            handle.close()
        self.handles.clear()
        self.writers.clear()

    def close(self):
        try:
            self.close_daily()
            self.log.flush()
            os.fsync(self.log.fileno())
        finally:
            self.log.close()


class Detector:
    def __init__(self, port, store):
        self.port, self.store = port, store
        self.connection = None
        self.buffer = bytearray()
        self.segment = 0
        self.header = {}
        self.role = None
        self.expected_role = None
        self.ready = False
        self.last_frame = None
        self.last_uptime = 0
        self.last_count = 0
        self.last_local = 0
        self.last_busy = 0
        self.saved_events = 0
        self.missing_events = 0
        self.deadline = 0
        self.retry_at = 0
        self.stale_reported = False
        self.preview = 0
        self.saw_boot = False

    def close(self):
        if self.connection:
            try:
                self.connection.close()
            finally:
                self.connection = None
        self.ready = False

    def connect(self, serial, initial=False):
        self.connection = serial.Serial(self.port, 115200, timeout=0,
                                        xonxoff=False, rtscts=False, dsrdtr=False)
        self.buffer.clear()
        self.ready = False
        self.header = {}
        self.role = None
        self.saw_boot = False
        self.deadline = time.monotonic() + 30
        if not initial:
            # Explicit reset on reconnection ensures a new boot header and segment.
            # It interrupts that board's acquisition and is always logged.
            self.store.operation("reconnect_reset_requested", port=self.port)
            self.connection.dtr = False
            time.sleep(0.1)
            self.connection.reset_input_buffer()
            self.connection.dtr = True
        self.store.operation("port_opened", port=self.port, initial=initial)

    def disconnect(self, reason):
        self.store.operation("port_disconnected", port=self.port, reason=str(reason),
                             last_received_monotonic_s=self.last_frame)
        self.close()
        self.retry_at = time.monotonic() + 10

    def healthy(self, now):
        return bool(self.connection and self.ready and self.last_frame is not None
                    and now - self.last_frame < 30)

    def feed(self, data, utc, mono_ns):
        self.store.raw(self.port, data, utc, mono_ns)
        self.buffer.extend(data)
        if len(self.buffer) > 65536:
            raise ValueError(f"{self.port}: no valid line boundaries; raw bytes preserved.")
        while b"\n" in self.buffer:
            line, _, rest = self.buffer.partition(b"\n")
            self.buffer = bytearray(rest)
            self.line(bytes(line).rstrip(b"\r"), utc, mono_ns)

    def line(self, raw, utc, mono_ns):
        location = raw.find(TITLE)
        if location >= 0:
            self.saw_boot = True
            self.segment += 1
            self.header = {}
            self.role = None
            self.ready = False
            self.last_uptime = self.last_count = self.last_local = self.last_busy = 0
            self.last_frame = None
            self.deadline = time.monotonic() + 30
            self.store.operation("device_boot", port=self.port, segment=self.segment,
                                 received_utc=utc, receive_monotonic_ns=mono_ns)
            return
        if b"60-second acquisition" in raw or b"standalone detector test" in raw:
            raise ValueError(f"{self.port}: older/test firmware. Upload the supplied CONTINUOUS sketch.")
        if not self.saw_boot:
            if raw and self.preview < 5:
                print(f"Before header [{self.port}]: {ascii(raw[:160])}")
                self.preview += 1
            return
        try:
            line = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(f"{self.port}: corrupt serial text; original bytes preserved.") from exc
        if not line:
            return
        if line.startswith("# "):
            if line == "# START":
                required = {"firmware": "CW_CONT_1.0.0", "signal_threshold": "50", "reset_threshold": "25",
                            "coincidence_method": "original_D6_level_two_A3_reads;window_not_calibrated"}
                if any(self.header.get(k) != v for k, v in required.items()) or self.role not in ROLES:
                    raise ValueError(f"{self.port}: incomplete or unexpected continuous firmware header.")
                self.ready = True
                self.store.operation("device_started", port=self.port, segment=self.segment, header=self.header)
            elif "=" in line:
                key, value = line[2:].split("=", 1)
                if self.ready or key in self.header:
                    raise ValueError(f"{self.port}: repeated metadata without a new boot header.")
                self.header[key] = value
                if key == "role":
                    if value not in ROLES or (self.expected_role and value != self.expected_role):
                        raise ValueError(f"{self.port}: unexpected role change; identify the connected detector.")
                    self.role = self.expected_role = value
            return
        if not self.ready:
            raise ValueError(f"{self.port}: event/status before a valid START header.")
        fields = line.split(",")
        if fields[0] not in ("E", "S") or len(fields) != (8 if fields[0] == "E" else 6):
            raise ValueError(f"{self.port}: invalid event/status record.")
        if any(not re.fullmatch(r"[0-9]+", v) for v in fields[1:]):
            raise ValueError(f"{self.port}: invalid numeric value.")
        values = list(map(int, fields[1:]))
        if fields[0] == "E":
            count, uptime, trigger, sample, local, busy, temp = values
        else:
            uptime, count, local, busy, high = values
        # Shared checks prevent counter wrap/reset from silently entering one segment.
        if uptime < self.last_uptime or local < self.last_local or busy < self.last_busy or busy > uptime + 1000:
            raise ValueError(f"{self.port}: counters went backwards or busy time is invalid; possible unmarked reset.")
        if local < count:
            raise ValueError(f"{self.port}: local count is smaller than accepted count.")
        if self.role == "MASTER_SINGLES" and local != count:
            raise ValueError(f"{self.port}: master local/accepted counts disagree.")
        if fields[0] == "E":
            if count <= self.last_count or not 50 < trigger <= 1023 or not 0 <= sample <= 1023 or not 0 <= temp <= 1023:
                raise ValueError(f"{self.port}: invalid/repeated event.")
            missing = count - self.last_count - 1
            if missing:
                self.missing_events += missing
                self.store.operation("missing_event_rows", port=self.port, segment=self.segment, missing=missing)
            self.store.csv("master_events" if self.role == "MASTER_SINGLES" else "slave_events", EVENT_FIELDS,
                           [utc, mono_ns, self.port, self.segment, self.role, self.header.get("detector", ""),
                            count, uptime, trigger, sample, local, busy, temp, missing], utc)
            self.saved_events += 1
        else:
            if count < self.last_count or high not in (0, 1):
                raise ValueError(f"{self.port}: invalid status counters.")
            missing = count - self.last_count
            if missing:
                self.missing_events += missing
                self.store.operation("missing_event_rows", port=self.port, segment=self.segment, missing=missing)
            self.store.csv("device_status", STATUS_FIELDS,
                           [utc, mono_ns, self.port, self.segment, self.role, uptime, count, local, busy, high], utc)
        self.last_count, self.last_uptime = count, uptime
        self.last_local, self.last_busy = local, busy
        if self.stale_reported:
            self.store.operation("serial_activity_restored", port=self.port)
            self.stale_reported = False
        self.last_frame = mono_ns / 1e9


def load_config(path):
    config = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not config.get("location", {}).get("city") or not config["location"].get("country"):
        raise ValueError("Set the measurement location in study_config.json.")
    geometry = config["geometry"]
    for key in ["master_width_mm", "master_length_mm", "master_thickness_mm", "slave_width_mm",
                "slave_length_mm", "slave_thickness_mm", "active_surface_separation_mm"]:
        if isinstance(geometry[key], bool) or not isinstance(geometry[key], (int, float)) or not math.isfinite(geometry[key]) or geometry[key] <= 0:
            raise ValueError(f"Invalid geometry: {key}")
    return config


def choose_ports(explicit, list_ports):
    available = list(list_ports.comports())
    print("Available ports:")
    for p in available:
        print(f"  {p.device}: {p.description}")
    ports = explicit or input("Enter TWO detector ports, e.g. COM3 COM4: ").split()
    ports = [p.upper().strip() for p in ports]
    if len(ports) != 2 or len(set(ports)) != 2 or any(not re.fullmatch(r"COM[1-9][0-9]*", p) for p in ports):
        raise ValueError("Enter two distinct COM ports.")
    hardware = [{"port": p.device, "description": p.description, "hwid": p.hwid,
                 "usb_serial_number": p.serial_number, "vid": p.vid, "pid": p.pid}
                for p in available if p.device.upper() in ports]
    return ports, hardware


def stay_awake(enable):
    if os.name != "nt":
        return False
    return bool(ctypes.windll.kernel32.SetThreadExecutionState(0x80000001 if enable else 0x80000000))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    duration = parser.add_mutually_exclusive_group()
    duration.add_argument("--days", type=float, default=10)
    duration.add_argument("--test-minutes", type=float)
    parser.add_argument("--ports", nargs=2)
    parser.add_argument("--config", type=Path, default=Path(__file__).resolve().with_name("study_config.json"))
    parser.add_argument("--output-dir", type=Path, default=Path("recordings"))
    parser.add_argument("--allow-sleep", action="store_true", help="Disable the temporary request to prevent idle sleep.")
    args = parser.parse_args()
    seconds = args.test_minutes * 60 if args.test_minutes is not None else args.days * 86400
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError("Duration must be positive and finite.")
    config = load_config(args.config)
    import serial
    from serial.tools import list_ports
    ports, hardware = choose_ports(args.ports, list_ports)
    print("Location:", config["location"])
    print("Geometry (mm):", config["geometry"])
    if not config["geometry"].get("measured_and_verified", False):
        print("Geometry is marked UNVERIFIED. Measure active dimensions/separation and edit study_config.json before the study.")
        if args.test_minutes is None:
            raise ValueError("For the study, set measured_and_verified to true only after measuring. --test-minutes permits setup tests.")
    input("Close Arduino IDE. Check Windows date/time is correct, connect both detectors, then press Enter: ")
    root = args.output_dir.expanduser().resolve() / ("study_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ_") + uuid4().hex[:6])
    root.mkdir(parents=True)
    metadata = {"schema": "CW_STUDY_LOG_V1", "created_utc": utc_now(), "requested_seconds": seconds,
                "test_run": args.test_minutes is not None, "configuration": config, "ports": hardware,
                "utc_source": "Windows system UTC at serial chunk receipt; NOT precision event UTC",
                "device_time": "Independent Nano 64-bit extended microsecond uptime; no UTC synchronization",
                "code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "status": "starting", "idle_sleep_prevention_requested": not args.allow_sleep}
    meta_path = root / "session.json"
    meta_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    # Preserve the exact software/config used alongside the original data.
    source_dir = root / "software"
    source_dir.mkdir()
    shutil.copy2(Path(__file__), source_dir / Path(__file__).name)
    shutil.copy2(args.config, source_dir / "study_config.json")
    for folder in ("CosmicWatch_Continuous_Master", "CosmicWatch_Continuous_Slave"):
        source = Path(__file__).resolve().parent / folder
        if source.is_dir():
            shutil.copytree(source, source_dir / folder)
    store = Storage(root)
    detectors = [Detector(p, store) for p in ports]
    awake = False
    began = None
    exit_status = "failed"
    healthy_seconds = 0.0
    last_health = None
    try:
        for d in detectors:
            d.connect(serial, initial=True)
        input("Both ports are open. Press Enter; then reset BOTH boards only when READY appears: ")
        for d in detectors:
            d.connection.reset_input_buffer()
            d.deadline = time.monotonic() + 30
        if not args.allow_sleep:
            awake = stay_awake(True)
            store.operation("idle_sleep_request", granted=awake,
                            note="Does not prevent lid-close, manual sleep, reboot or power loss.")
        print("READY: press and release BOTH physical RESET buttons once, close together.", flush=True)
        store.operation("waiting_for_pair", ports=ports)
        last_loop = time.monotonic()
        last_offset = time.time() - last_loop
        next_health = last_loop
        next_print = last_loop + 60
        next_disk = last_loop
        startup_limit = last_loop + 40
        while True:
            now = time.monotonic()
            gap = now - last_loop
            if gap > 5:
                store.operation("host_polling_gap", seconds=gap, note="Possible sleep/stall; data coverage uncertain.")
            if began is not None and last_health and gap <= 5:
                healthy_seconds += min(gap, max(0, began + seconds - last_loop))
            offset = time.time() - now
            if abs(offset - last_offset) > 1:
                store.operation("system_clock_adjustment", change_seconds=offset-last_offset,
                                note="Review UTC bins; monotonic time remains separate.")
            last_offset, last_loop = offset, now
            if began is not None and now >= began + seconds:
                exit_status = "requested_duration_elapsed"
                break
            for d in detectors:
                if d.connection is None and now >= d.retry_at:
                    try:
                        d.connect(serial)
                    except serial.SerialException as exc:
                        d.close()
                        d.retry_at = now + 10
                        store.operation("reconnect_failed", port=d.port, reason=str(exc))
                if d.connection:
                    try:
                        data = d.connection.read(min(d.connection.in_waiting, 8192))
                    except serial.SerialException as exc:
                        d.disconnect(exc)
                        continue
                    if data:
                        d.feed(data, utc_now(), time.monotonic_ns())
                    if not d.ready and now > d.deadline:
                        # Wrong/no firmware must not be misreported as a zero-event run.
                        if began is None:
                            raise TimeoutError(f"{d.port}: no continuous START within 30 s. Check reset/firmware. Raw bytes preserved.")
                        d.disconnect("No continuous START after reconnection")
                    if d.ready and d.last_frame is not None and now - d.last_frame > 30 and not d.stale_reported:
                        d.stale_reported = True
                        store.operation("serial_silence", port=d.port,
                                        last_received_monotonic_s=d.last_frame, note="Coverage uncertain, not a zero count.")
            roles = [d.role for d in detectors if d.role]
            if len(roles) == 2 and set(roles) != ROLES:
                raise ValueError("Both devices have the same role. Upload one continuous master and one continuous slave.")
            healthy = all(d.healthy(time.monotonic()) for d in detectors)
            if began is None and healthy:
                began = time.monotonic()
                metadata["pair_ready_utc"] = utc_now()
                metadata["pair_ready_monotonic_ns"] = time.monotonic_ns()
                metadata["status"] = "recording"
                meta_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
                store.operation("study_timer_started", requested_seconds=seconds,
                                note="Wall duration from first healthy pair; gaps are NOT made up automatically.")
            if began is None and now > startup_limit:
                raise TimeoutError("No healthy pair after 40 seconds; check both reset buttons and continuous firmware.")
            if healthy != last_health:
                store.operation("pair_link_state", both_links_healthy=healthy,
                                last_received_monotonic_s={d.port:d.last_frame for d in detectors})
                last_health = healthy
            if now >= next_health:
                utc = utc_now()
                row = [utc, time.monotonic_ns(), int(healthy)]
                for d in detectors:
                    row.extend([d.port, d.role or "", "" if d.last_frame is None else max(0, now-d.last_frame)])
                store.csv("host_health", HEALTH_FIELDS, row, utc)
                next_health = now + 10
            if now >= next_print:
                print("SAVED EVENT ROWS: " + "; ".join(f"{d.role or d.port}: {d.saved_events}" for d in detectors), flush=True)
                next_print = now + 60
            if now >= next_disk:
                if shutil.disk_usage(root).free < 200 * 1024 * 1024:
                    raise OSError("Less than 200 MB free. Stopping before disk fills; preserve this session.")
                next_disk = now + 60
            store.flush()
            time.sleep(0.01)
    except KeyboardInterrupt:
        exit_status = "user_stopped"
        store.operation("user_stopped")
    except BaseException as exc:
        store.operation("logger_error", error=repr(exc))
        raise
    finally:
        # Closing/reopening USB may reset boards. Every restart uses a new session folder.
        for d in detectors:
            d.close()
        if awake:
            stay_awake(False)
        metadata.update({"status": exit_status, "closed_utc": utc_now(),
                         "elapsed_host_seconds": 0 if began is None else time.monotonic()-began,
                         "estimated_healthy_link_seconds": healthy_seconds,
                         "coverage_note": "Host-link estimate only; NOT calibrated detector live time. Review status/gaps and raw data.",
                         "saved_events": {d.port:d.saved_events for d in detectors},
                         "missing_event_rows": {d.port:d.missing_events for d in detectors},
                         "detector_summary": [{"port":d.port, "role":d.expected_role,
                                               "segments":d.segment, "saved_events":d.saved_events,
                                               "missing_event_rows":d.missing_events} for d in detectors]})
        meta_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
        store.operation("session_closed", status=exit_status, saved_events=metadata["saved_events"])
        store.close()
        print(f"\nData preserved in: {root}\nKeep the WHOLE session folder, including raw files and metadata.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"\nERROR: {exc}\nClose Arduino IDE if a COM port is busy. For missing serial: install requirements.txt.", file=sys.stderr)
        sys.exit(1)

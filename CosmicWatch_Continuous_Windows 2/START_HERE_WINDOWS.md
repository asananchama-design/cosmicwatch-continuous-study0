# Continuous CosmicWatch study: Windows setup

This package replaces the earlier 60-second test workflow. It records a master/slave pair for **10 days by default**, using the Windows computer for UTC receipt timestamps and file storage. It does not use an SD card.

Location is preset to **Skyline College, San Bruno, California, United States**. File paths contain no personal username. Extract the package into a writable folder you choose on the Windows computer.

## 1. Upload the NEW continuous Arduino sketches

Both detectors need new firmware, even if the earlier one-minute program worked.

1. Stop any recorder with **Ctrl + C**. Disconnect both detectors from power and disconnect the coincidence cable.
2. Connect only the intended master to USB.
3. In Arduino IDE, open `CosmicWatch_Continuous_Master/CosmicWatch_Continuous_Master.ino`. Keep its companion `Clock64.h` in the same folder.
4. Select **Arduino Nano**, its COM port, and the correct ATmega328P processor/bootloader. Use **Old Bootloader** if that is what previously worked for this board. Upload and wait for success.
5. Unplug it. Connect only the intended slave, then upload `CosmicWatch_Continuous_Slave/CosmicWatch_Continuous_Slave.ino` with its companion `Clock64.h`.
6. Label the physical detectors and note their COM ports. Close ALL Arduino IDE windows.

These sketches target original CosmicWatch v2 / 16 MHz ATmega328P Nano hardware, including its external analog-reference circuit. They are not generic firmware for other detector models. The original D6 coincidence sequence is retained; the actual window has not been calibrated on your boards.

## 2. Measure the geometry and edit the setup file

Open `study_config.json` in Notepad. Fill in:

- Room/floor, indoor/outdoor location and any shielding above the detectors.
- Unique physical labels for the master and slave.
- Actual active scintillator width, length, thickness and surface separation in **millimeters**.

Nominal original v2 dimensions are prefilled as 50 x 50 x 10 mm, with 50 mm active-surface separation. They are assumptions until checked. Follow the study's **d = L** setup: aligned scintillators one above the other. Do not measure the separation between outer cases instead of active surfaces. Record which active surfaces define your measured distance in the notes.

After physically verifying and updating those numbers, change:

```json
"measured_and_verified": false
```

to:

```json
"measured_and_verified": true
```

Save the file without changing its `.json` extension. The ten-day command requires verified geometry. Short setup tests are allowed while this flag is false and are labeled as tests.

## 3. Prepare the Windows computer

Install Python if needed from [python.org](https://www.python.org/downloads/windows/). In File Explorer, open this extracted package folder. Click the address bar, type `powershell`, and press Enter.

Run these commands separately:

```powershell
py -m venv .venv
```

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

The dependency is installed inside this project folder; activation scripts and administrator access are not normally required. If `py` is unavailable but `python --version` works, use `python -m venv .venv` for the first command.

Before recording, confirm the Windows date, time and time synchronization are correct. Keep the computer plugged into power, the laptop lid open, and the recording window open. The script requests temporary prevention of idle sleep and releases that request when it exits. It cannot prevent manual sleep, closing the lid, an operating-system restart, unplugging, or power failure. Arrange a suitable uninterrupted period on the shared computer.

## 4. Connect and perform a short test

With both detectors unpowered, connect the proper coincidence cable fully at both ends. Put them in the fixed, verified measurement geometry. Connect each detector to the Windows computer with a USB DATA cable. This assumes original unmodified v2/Nano hardware; the coincidence cable also carries power.

Run a five-minute check:

```powershell
.\.venv\Scripts\python.exe .\record_study_windows.py --test-minutes 5 --output-dir ".\recordings"
```

1. The program lists COM ports. Enter your two detector ports, such as `COM3 COM4`, using the actual numbers.
2. Follow the Enter prompts.
3. Only when **READY** appears, press and release BOTH physical RESET buttons once, close together.
4. Confirm it identifies one `MASTER_SINGLES` and one `SLAVE_COINCIDENCE` in the device-start messages, and reports `both_links_healthy: true`.
5. Leave the setup alone. The logger prints saved event totals about once per minute and stops after five minutes from the first healthy pair.

Check the resulting session folder for event CSVs, status CSVs, raw files, and `session.json`. If there were genuinely no slave events, a slave event CSV may not exist; the status counters and session summary document that zero. No heartbeat/START is a startup problem, not a zero count.

For a longer pilot, repeat with `--test-minutes 90`. This exceeds the underlying microsecond timer's approximately 72-minute rollover interval. The new firmware and Windows hardware need this local check before an unattended long run.

## 5. Start the study recording

After the test and geometry verification, use:

```powershell
.\.venv\Scripts\python.exe .\record_study_windows.py --days 10 --output-dir ".\recordings"
```

Select both COM ports, follow the prompts, and reset BOTH detectors once at READY. Do not reset or reposition them during normal recording. Do not open Serial Monitor while the logger is running.

The ten-day timer begins once both devices are producing valid continuous data/status. It is a **host elapsed-duration limit**, not a promise of ten uninterrupted days of exposure. Connection gaps are logged and are not automatically added to the end. The final status says `requested_duration_elapsed`, not “perfect continuous data.” Check interruptions before claiming continuous exposure.

To stop early, press **Ctrl + C** once and let the script finish saving. It is okay to stop and preserve a shorter session; coordinate shorter datasets with the research group. Running the command again creates a NEW session folder and requires another paired reset.

The Windows logger controls the recording duration. The new Arduino firmware itself runs continuously while powered; LEDs may still flash after the computer has stopped saving. This is expected.

## 6. Where data are saved

Everything goes inside the folder you chose:

```text
recordings/
  study_UTC_DATE_TIME_UNIQUE_ID/
    session.json
    operations.jsonl
    software/
    YYYY-MM-DD_master_events.csv
    YYYY-MM-DD_slave_events.csv
    YYYY-MM-DD_device_status.csv
    YYYY-MM-DD_host_health.csv
    YYYY-MM-DD_raw_chunks.csv
    YYYY-MM-DD_COM3_raw.bin
    YYYY-MM-DD_COM4_raw.bin
```

Dates are UTC, so files change at UTC midnight, not California midnight. Actual COM names vary. Event files are created when their first event is saved. This avoids producing misleading empty event files without a working detector.

Keep the **whole session folder**. Preserve original raw files unchanged; use copies for analysis. Copy completed daily files to a backup location regularly. The computer needs free local storage; the script stops if free space drops below 200 MB. Unexpected power loss can still lose the latest unsynced data; there is no on-detector storage backup.

## 7. What the timestamps and counters mean

- `received_utc`: Windows system UTC when a serial chunk was received, with `Z` indicating UTC. Several lines in one chunk may share this timestamp.
- `host_monotonic_ns`: separate computer monotonic timestamp useful for ordering and detecting wall-clock changes.
- `device_uptime_us`: 64-bit extended Nano uptime since its current boot segment. The code handles the underlying 32-bit `micros()` rollover. This clock is independent on each Nano and is not calibrated UTC.
- `segment`: increments when a new boot header is received, so a reset is not silently mixed with the previous device timeline.
- `event_id`: accepted event counter within a segment. Missing IDs are logged, not filled in with invented data.
- `local_triggers`: all local triggers; the slave's event rows contain only those accepted by coincidence.
- `busy_us_before_event` / `busy_us`: measured local processing/re-arm busy time. These are not the combined live time of the two detectors.
- `adc_trigger` and `adc_sample`: raw ADC values. They are not calibrated particle energies.
- `temp_adc`: raw detector-board temperature-sensor reading. It is not a calibrated ambient atmospheric temperature measurement.

UTC receipt times satisfy an explicit absolute time basis for recorded events, but are **estimates of event time**, affected by USB buffering, computer scheduling and the computer clock. This package does not provide GPS timing or precision inter-detector synchronization. Share this timestamp definition with the research group to confirm it meets their analysis needs.

The sketches send status approximately every 10 seconds even if there are no events. This helps distinguish no coincidences from a lost connection. Status output introduces small local acquisition dead time; it is included in subsequent local busy totals.

## 8. Interruptions and field notes

The logger preserves raw bytes before parsing. It logs device boots, connection loss/reconnection, missing event rows, serial silence, host polling stalls and detected system-clock jumps in `operations.jsonl`.

On a serial disconnection, it tries the same COM port every 10 seconds and requests a reset of that board after reopening it. That interruption and reset are logged. It does not silently assume a newly connected different-role detector is equivalent. If the COM number changes or the firmware is wrong, operator action may be needed.

The logger cannot detect a loosened coincidence cable or physical repositioning reliably. Record those manually. In a SECOND PowerShell window in the package folder, use this template, replacing the session folder name:

```powershell
.\.venv\Scripts\python.exe .\add_field_note.py --session ".\recordings\study_YOUR_SESSION_FOLDER" --kind interruption_start --note "Stopped acquisition to adjust the support."
```

Use `--kind interruption_end` when the interruption ends, `--kind reposition` for a physical move, or `--kind observation` for other notes. The note is automatically UTC stamped. If entering a note later, provide its actual occurrence time with `--occurred-utc "YYYY-MM-DDTHH:MM:SSZ"`. Notes go into a separate `field_notes.jsonl`; raw data are not edited.

If PowerShell or Windows crashes, already-written files remain. A session still marked `recording` without a `closed_utc` was not cleanly closed: preserve it as interrupted raw data and restart into a new session. It must be reviewed before analysis; do not invent a complete run by editing counters or timestamps.

## 9. Graph the new files in Colab

Use the included **`CosmicWatch_Continuous_Colab.ipynb`**, not the old 60-second notebook.

1. In Colab, choose **File → Upload notebook** and upload this notebook.
2. Choose **Runtime → Run all**.
3. Select one cleanly stopped study folder's `session.json`, ALL daily `*_master_events.csv` and `*_slave_events.csv`, and ALL daily `*_host_health.csv`. Hold Ctrl to select multiple files on Windows. No event CSV is needed for a role whose saved count is zero.
4. The notebook checks event totals, plots cumulative counts, recorded counts by UTC hour, and a host-link diagnostic. It downloads plots and a summary ZIP.

Use all daily files from ONE session at a time. Do not upload `.bin` files to this notebook. The supplied `colab_continuous.py` can alternatively be pasted into one blank Colab code cell.

These are exploratory recorded-count graphs. First/last hours may be partial, missing data can mimic a low rate, and link-health estimates are not detector live-time measurements. Review operations, raw data, device status and field notes before scientific comparisons. Counts have not been corrected for geometry, detector efficiency, dead time or pressure. Master singles and slave coincidences remain different event selections.

## 10. Data to give the research group

- Original raw logs and event/status files, with the timestamp definition above.
- Location: Skyline College, San Bruno, California, USA; add room/site details.
- Actual active scintillator dimensions, separation, orientation and physical detector IDs.
- Firmware/settings and coincidence method, including that the exact window is uncalibrated.
- Start/end times, interruptions, repositioning and any lost-data indicators.
- Atmospheric pressure and ambient temperature, with timestamps and units, if independently available. State station pressure versus sea-level-adjusted pressure when applicable. These optional measurements are not supplied by this logger.

## Troubleshooting

- **Access denied / port busy:** close all Arduino IDE windows and stop other serial recorders with Ctrl + C.
- **Older firmware:** upload BOTH new continuous sketches. A program that prints “60-second acquisition” is incompatible.
- **No continuous START:** reset BOTH at READY, check actual COM ports and 115200 baud. Inspect the saved raw logs; a header is required even when there are zero events.
- **No slave coincidences:** inspect slave local-trigger status, master activity, alignment and coincidence cable. A zero count alone does not prove damage.
- **Geometry unverified:** measure the active dimensions/separation, update `study_config.json`, then set `measured_and_verified` to true. Short `--test-minutes` runs can be used before this.
- **Protocol/data error:** the logger stops with an error and preserves raw files. Keep the failed session for diagnosis; do not label it complete.
- **No Python or serial module:** follow section 3 in this extracted folder.

## Validation and attribution

The firmware compiled for both original Nano roles. Host simulations exercised each role for more than 10 simulated days, including multiple timer rollovers, re-arm behavior, coincidence gating and status during a sustained HIGH input. The logger passed simulated tests of parsing, zero events, resets, missing rows, day changes, UTC date reversal, raw preservation, reconnection and clean termination. This is not a ten-day physical Windows hardware test or detector calibration. See `VALIDATION.md` for the final check record.

Based on the [CosmicWatch v2 project](https://github.com/spenceraxani/CosmicWatch-Desktop-Muon-Detector-v2) and the user-supplied International Cosmic Day measurement guide. Preserve attribution and review the upstream license when redistributing derived firmware.

Additional technical references: [pySerial](https://pyserial.readthedocs.io/en/latest/pyserial_api.html), [Windows idle-sleep request](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-setthreadexecutionstate), and [Arduino micros](https://docs.arduino.cc/language-reference/en/functions/time/micros/).

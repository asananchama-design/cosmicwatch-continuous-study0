# Validation record

Prepared November 19, 2026. This records software verification, not detector calibration or a physical ten-day trial.

## Arduino compile checks

Target: `arduino:avr:nano:cpu=atmega328old`, Arduino AVR core 1.8.8.

| Role | Flash | Static RAM |
| --- | ---: | ---: |
| Master | 5460 bytes | 297 bytes |
| Slave | 5626 bytes | 297 bytes |

Both compiled successfully. Each sketch uses its matching `Clock64.h`.

## Firmware host simulations

Both roles ran through more than 10 simulated days (11 days in the harness), with repeated 32-bit timer rollovers. Assertions covered:

- Extended uptime remains monotonic across wraps.
- Re-arm at the lower threshold; sustained high input is not counted repeatedly.
- Status continues while awaiting re-arm.
- Master records local triggers; slave rejects a trigger when the simulated coincidence gate is low.
- Master coincidence output returns low after its pulse.
- Actual text emitted by the simulated firmware is accepted by the Windows parser.

Host mocks do not validate physical pulse width, analog response, real serial/USB behavior or Nano oscillator accuracy.

## Windows logger simulations

16 tests passed, including fragmented two-port protocol input, exact preservation of raw bytes, healthy zero-event operation, timestamps exceeding both 32 bits and ten days, reset segmentation, unexpected role rejection, missing-event accounting, duplicate/corrupt records, wrong firmware, UTC-day file rotation including a backward date change, configuration loading, and a full logger run with a simulated port disconnection and successful reconnection. The full-run test verified both connections close and final metadata is written.

## Colab checks

Synthetic input exercised graph rendering and PNG/PDF/CSV/ZIP export; a zero-event slave; both roles with zero events; and rejection of missing or duplicate uploads. A representative rendered figure was visually inspected. Synthetic data and plots are not included as experimental results.

## Physical validation still required

- Upload both continuous sketches and run a five-minute test on the actual Windows computer.
- Verify both role headers, regular status messages, event counters, UTC clock and saved files.
- Run a 90-minute pilot before leaving the setup unattended; this covers the underlying approximately 72-minute micros rollover on physical hardware.
- Confirm geometry and preserve all raw files and metadata.

The software has not been run for ten real days on the user's Windows machine. UTC receipt timestamps are not precision particle-arrival timestamps. No absolute efficiency, clock drift, geometric acceptance or coincidence-window calibration is claimed.

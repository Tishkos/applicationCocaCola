# Safety

The plant runs exactly the same with this program switched off. It is never in the control
path. The InteliNeo, the genset controllers and the SAU keep full authority at all times.

## What makes it safe

- **Read-only by construction.** The Modbus adapter contains only `read_holding_registers`
  and `read_input_registers`. There is no write function, no command concept, no
  endpoint that accepts a change. `tests\test_collector.py` checks that this stays true.
- **Gentle on the SAU.** The SAU gateway already serves the InteliNeo and is known to stall
  under load. The collector reads each device as one or two contiguous blocks, once per
  `poll_interval_s` (3600 by default), with a short timeout, and never retries in a loop.
- **Nothing else should poll the SAU.** Grafana, Turkey, Excel users all read the collector,
  not the gateway. Keep the SAU web page closed when not needed.
- **Honest data.** A device that does not answer is stored as `bad` with `null`, never as
  zero, so an outage can never look like "0 kW produced".
- **Local only.** The dashboard listens on the plant PC. Nothing leaves the site unless someone
  copies a file or the plant decides to open the port/VPN for Turkey.
- **Passwords stay on site.** The InteliSCADA site password (if that adapter is ever used) goes
  into `config\site.plant.yaml` on the plant PC only. Controller and SAU passwords are not needed.

## Operating procedure

1. First `CHECK-PLANT.bat` with the real addresses: compare every value with the SAU web
   page / controller screen; fix scale, sign, word order in the profile.
2. Watch the InteliNeo for a day after `START-PLANT.bat`: no new "PV logger comm fail" events
   should appear that were not there before. If they do, stop the collector (`STOP.bat`) and
   raise `poll_interval_s`.
3. Changes to the controller configuration (e.g. the 250 ms "pause before request" on the PV
   logger groups) are a separate maintenance action by the plant / integrator, with a backup
   first. This program does not do them.

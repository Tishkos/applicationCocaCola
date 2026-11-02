"""
Modbus TCP response-time test against the SAU gateway. READ-ONLY.
One read request per second; logs every result to CSV; prints slow/failed ones.

usage: python sau_latency.py <sau_ip> <unit_id> <register_addr> <out.csv>
"""

import csv
import statistics
import sys
import time
from datetime import datetime

from pymodbus.client import ModbusTcpClient

host = sys.argv[1] if len(sys.argv) > 1 else "192.168.0.128"
unit = int(sys.argv[2]) if len(sys.argv) > 2 else 1
addr = int(sys.argv[3]) if len(sys.argv) > 3 else 0
out = sys.argv[4] if len(sys.argv) > 4 else "modbus_latency.csv"

client = ModbusTcpClient(host, port=502, timeout=5)
lat: list[float] = []
fails = 0
reconnects = 0

with open(out, "a", newline="") as f:
    w = csv.writer(f)
    if f.tell() == 0:
        w.writerow(["time", "ok", "ms", "detail"])
    print(f"latency test -> {host} unit {unit} addr {addr}; writing {out}")
    if not client.connect():
        print("initial connect failed; will keep retrying")
    try:
        while True:
            t0 = time.perf_counter()
            try:
                rr = client.read_holding_registers(address=addr, count=2, device_id=unit)
                ms = (time.perf_counter() - t0) * 1000
                if rr is None or rr.isError():
                    fails += 1
                    w.writerow([datetime.now().isoformat(timespec="milliseconds"), 0, round(ms, 1), str(rr)])
                    print(datetime.now().strftime("%H:%M:%S"), f"FAIL {ms:.0f} ms  {rr}")
                    if not client.connected:
                        reconnects += 1
                        client.connect()
                else:
                    lat.append(ms)
                    w.writerow([datetime.now().isoformat(timespec="milliseconds"), 1, round(ms, 1), rr.registers])
                    if ms > 500:
                        print(datetime.now().strftime("%H:%M:%S"), f"slow {ms:.0f} ms")
            except Exception as exc:
                fails += 1
                ms = (time.perf_counter() - t0) * 1000
                w.writerow([datetime.now().isoformat(timespec="milliseconds"), 0, round(ms, 1), f"exc {exc}"])
                print(datetime.now().strftime("%H:%M:%S"), f"ERROR {exc}")
                reconnects += 1
                time.sleep(2)
                client.close()
                client.connect()
            f.flush()
            time.sleep(1.0)
    except KeyboardInterrupt:
        pass
    finally:
        client.close()
        if lat:
            s = sorted(lat)
            print(f"\nok={len(lat)} fails={fails} reconnects={reconnects} "
                  f"median={statistics.median(lat):.0f} ms p95={s[int(0.95*len(s))-1]:.0f} ms max={max(lat):.0f} ms")
        else:
            print(f"\nno successful reads; fails={fails}. Check unit id / register address with Sineng.")

# ---- edit these if the site addresses differ ----------------------------------
$SAU_IP        = "192.168.0.128"
$CONTROLLER_IP = "192.168.0.112"

# SAU diagnostic page. Default is the web UI root; if the connections table is served
# by a separate URL (see README, "Note on the SAU page sampling"), paste it here.
$SAU_DIAG_URL  = "http://$SAU_IP/"
$SAU_COOKIE    = ""          # e.g. "JSESSIONID=abc123"  (leave empty if no login needed)

# Modbus unit id and a register that is known to be readable on the SAU.
# 0 / count 2 usually works; if the latency test shows only FAIL, ask Sineng for
# any readable holding-register address and put it here.
$MODBUS_UNIT   = 1
$MODBUS_ADDR   = 0

# Output folder and capture file rotation (seconds per file)
$OUT_ROOT      = "C:\sau_diag"
$CAPTURE_ROTATE_S = 600

# Path to tshark (default Wireshark install location)
$TSHARK = "C:\Program Files\Wireshark\tshark.exe"

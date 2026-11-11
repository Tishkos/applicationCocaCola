# Modbus TCP register map for the CCI / DT Turkey platform

Server: the plant data collector on the plant PC — `<plant-pc-ip>:502`, unit id `1`, Modbus TCP, **read-only** (FC03 Read Holding Registers; FC04 returns the same registers; every write is refused with an exception).

The values are the collector's last read of the site devices: **every 10 minutes** (it can be set to 30 minutes, 1 hour or 2 hours on site). Please poll every 10 minutes or slower; faster polling only returns the same values again. Registers 0–2 give the time of the last site read, so you can see how fresh the values are. Polling never reaches the SAU or the InteliNeo.

Register map version 2 (version 1 plus the analyzer block below; every register of version 1 is unchanged). Addresses are 0-based protocol addresses (address 100 = holding register 40101 in 1-based notation).

## Header

| Address | Type | Meaning |
|---|---|---|
| 0–1 | uint32 (high word first) | Unix time (UTC seconds) of the last site read |
| 2 | uint16 | Minutes since the last site read (65535 = never) |
| 3 | uint16 | Number of tags with quality *good* |
| 4 | uint16 | Total number of tags (305) |
| 5 | uint16 | Register map version |
| 6 | uint16 | Heartbeat, +1 at every site read |
| 7 | uint16 | Analyzers published (slots in use), map version 2 |
| 8 | uint16 | Analyzer slots (48), map version 2 |

## Encoding

- Float tags: **float32**, 2 registers, high word first (ABCD), engineering units as in the Unit column, scale 1.
- Boolean tags: **uint16**, 1 register, 0 or 1.
- Quality: one uint16 per tag at `1000 + index`: **1** good, **2** stale (older than two read intervals), **0** bad / device not answering / no data yet. While a tag is bad its value registers hold the last good value (configurable to zero or NaN).

## Tags

| # | Tag name | Address | Registers | Type | Unit | Quality address | Source | Status |
|---|---|---|---|---|---|---|---|---|
| 0 | INVERTER_1.INV1_AC_POWER | 100 | 2 | float32 | kW | 1000 | inv-01.ac_power_kw | Ready |
| 1 | INVERTER_1.INV1_CURRENT_I1 | 102 | 2 | float32 | A | 1001 | inv-01.current_l1_a | Ready |
| 2 | INVERTER_1.INV1_CURRENT_I2 | 104 | 2 | float32 | A | 1002 | inv-01.current_l2_a | Ready |
| 3 | INVERTER_1.INV1_CURRENT_I3 | 106 | 2 | float32 | A | 1003 | inv-01.current_l3_a | Ready |
| 4 | INVERTER_1.INV1_DC_POWER | 108 | 2 | float32 | kW | 1004 | inv-01.dc_power_kw | Ready |
| 5 | INVERTER_1.INV1_FREKANS | 110 | 2 | float32 | Hz | 1005 | inv-01.frequency_hz | Ready |
| 6 | INVERTER_1.INV1_VOLTAGE_V1 | 112 | 2 | float32 | V | 1006 | inv-01.voltage_l1_v | Ready |
| 7 | INVERTER_1.INV1_VOLTAGE_V2 | 114 | 2 | float32 | V | 1007 | inv-01.voltage_l2_v | Ready |
| 8 | INVERTER_1.INV1_VOLTAGE_V3 | 116 | 2 | float32 | V | 1008 | inv-01.voltage_l3_v | Ready |
| 9 | INVERTER_1.INV1_DAILY_PRODUCTION | 118 | 2 | float32 | kWh | 1009 | inv-01.energy_today_kwh | Ready |
| 10 | INVERTER_1.INV1_Connection_Error | 120 | 1 | boolean |  | 1010 | inv-01.comm_error | Ready |
| 11 | INVERTER_1.INV1_String_CURRENT_I1 | 121 | 2 | float32 | A | 1011 | inv-01.string_current_1_a | Ready |
| 12 | INVERTER_1.INV1_String_CURRENT_I2 | 123 | 2 | float32 | A | 1012 | inv-01.string_current_2_a | Ready |
| 13 | INVERTER_1.INV1_String_CURRENT_I3 | 125 | 2 | float32 | A | 1013 | inv-01.string_current_3_a | Ready |
| 14 | INVERTER_1.INV1_String_CURRENT_I4 | 127 | 2 | float32 | A | 1014 | inv-01.string_current_4_a | Ready |
| 15 | INVERTER_1.INV1_String_CURRENT_I5 | 129 | 2 | float32 | A | 1015 | inv-01.string_current_5_a | Ready |
| 16 | INVERTER_1.INV1_String_CURRENT_I6 | 131 | 2 | float32 | A | 1016 | inv-01.string_current_6_a | Ready |
| 17 | INVERTER_1.INV1_String_CURRENT_I7 | 133 | 2 | float32 | A | 1017 | inv-01.string_current_7_a | Ready |
| 18 | INVERTER_1.INV1_String_CURRENT_I8 | 135 | 2 | float32 | A | 1018 | inv-01.string_current_8_a | Ready |
| 19 | INVERTER_1.INV1_String_CURRENT_I9 | 137 | 2 | float32 | A | 1019 | inv-01.string_current_9_a | Ready |
| 20 | INVERTER_1.INV1_String_CURRENT_I10 | 139 | 2 | float32 | A | 1020 | inv-01.string_current_10_a | Ready |
| 21 | INVERTER_1.INV1_String_CURRENT_I11 | 141 | 2 | float32 | A | 1021 | inv-01.string_current_11_a | Ready |
| 22 | INVERTER_1.INV1_String_CURRENT_I12 | 143 | 2 | float32 | A | 1022 | inv-01.string_current_12_a | Ready |
| 23 | INVERTER_1.INV1_String_CURRENT_I13 | 145 | 2 | float32 | A | 1023 | inv-01.string_current_13_a | Ready |
| 24 | INVERTER_1.INV1_String_CURRENT_I14 | 147 | 2 | float32 | A | 1024 | inv-01.string_current_14_a | Ready |
| 25 | INVERTER_1.INV1_String_CURRENT_I15 | 149 | 2 | float32 | A | 1025 | inv-01.string_current_15_a | Ready |
| 26 | INVERTER_1.INV1_String_CURRENT_I16 | 151 | 2 | float32 | A | 1026 | inv-01.string_current_16_a | Ready |
| 27 | INVERTER_1.INV1_String_CURRENT_I17 | 153 | 2 | float32 | A | 1027 | inv-01.string_current_17_a | Ready |
| 28 | INVERTER_1.INV1_TOTAL_PRODUCTION | 155 | 2 | float32 | kWh | 1028 | inv-01.energy_total_kwh | Ready |
| 29 | INVERTER_2.INV2_AC_POWER | 157 | 2 | float32 | kW | 1029 | inv-02.ac_power_kw | Ready |
| 30 | INVERTER_2.INV2_CURRENT_I1 | 159 | 2 | float32 | A | 1030 | inv-02.current_l1_a | Ready |
| 31 | INVERTER_2.INV2_CURRENT_I2 | 161 | 2 | float32 | A | 1031 | inv-02.current_l2_a | Ready |
| 32 | INVERTER_2.INV2_CURRENT_I3 | 163 | 2 | float32 | A | 1032 | inv-02.current_l3_a | Ready |
| 33 | INVERTER_2.INV2_DC_POWER | 165 | 2 | float32 | kW | 1033 | inv-02.dc_power_kw | Ready |
| 34 | INVERTER_2.INV2_FREKANS | 167 | 2 | float32 | Hz | 1034 | inv-02.frequency_hz | Ready |
| 35 | INVERTER_2.INV2_VOLTAGE_V1 | 169 | 2 | float32 | V | 1035 | inv-02.voltage_l1_v | Ready |
| 36 | INVERTER_2.INV2_VOLTAGE_V2 | 171 | 2 | float32 | V | 1036 | inv-02.voltage_l2_v | Ready |
| 37 | INVERTER_2.INV2_VOLTAGE_V3 | 173 | 2 | float32 | V | 1037 | inv-02.voltage_l3_v | Ready |
| 38 | INVERTER_2.INV2_DAILY_PRODUCTION | 175 | 2 | float32 | kWh | 1038 | inv-02.energy_today_kwh | Ready |
| 39 | INVERTER_2.INV2_Connection_Error | 177 | 1 | boolean |  | 1039 | inv-02.comm_error | Ready |
| 40 | INVERTER_2.INV2_String_CURRENT_I1 | 178 | 2 | float32 | A | 1040 | inv-02.string_current_1_a | Ready |
| 41 | INVERTER_2.INV2_String_CURRENT_I2 | 180 | 2 | float32 | A | 1041 | inv-02.string_current_2_a | Ready |
| 42 | INVERTER_2.INV2_String_CURRENT_I3 | 182 | 2 | float32 | A | 1042 | inv-02.string_current_3_a | Ready |
| 43 | INVERTER_2.INV2_String_CURRENT_I4 | 184 | 2 | float32 | A | 1043 | inv-02.string_current_4_a | Ready |
| 44 | INVERTER_2.INV2_String_CURRENT_I5 | 186 | 2 | float32 | A | 1044 | inv-02.string_current_5_a | Ready |
| 45 | INVERTER_2.INV2_String_CURRENT_I6 | 188 | 2 | float32 | A | 1045 | inv-02.string_current_6_a | Ready |
| 46 | INVERTER_2.INV2_String_CURRENT_I7 | 190 | 2 | float32 | A | 1046 | inv-02.string_current_7_a | Ready |
| 47 | INVERTER_2.INV2_String_CURRENT_I8 | 192 | 2 | float32 | A | 1047 | inv-02.string_current_8_a | Ready |
| 48 | INVERTER_2.INV2_String_CURRENT_I9 | 194 | 2 | float32 | A | 1048 | inv-02.string_current_9_a | Ready |
| 49 | INVERTER_2.INV2_String_CURRENT_I10 | 196 | 2 | float32 | A | 1049 | inv-02.string_current_10_a | Ready |
| 50 | INVERTER_2.INV2_String_CURRENT_I11 | 198 | 2 | float32 | A | 1050 | inv-02.string_current_11_a | Ready |
| 51 | INVERTER_2.INV2_String_CURRENT_I12 | 200 | 2 | float32 | A | 1051 | inv-02.string_current_12_a | Ready |
| 52 | INVERTER_2.INV2_String_CURRENT_I13 | 202 | 2 | float32 | A | 1052 | inv-02.string_current_13_a | Ready |
| 53 | INVERTER_2.INV2_String_CURRENT_I14 | 204 | 2 | float32 | A | 1053 | inv-02.string_current_14_a | Ready |
| 54 | INVERTER_2.INV2_String_CURRENT_I15 | 206 | 2 | float32 | A | 1054 | inv-02.string_current_15_a | Ready |
| 55 | INVERTER_2.INV2_String_CURRENT_I16 | 208 | 2 | float32 | A | 1055 | inv-02.string_current_16_a | Ready |
| 56 | INVERTER_2.INV2_String_CURRENT_I17 | 210 | 2 | float32 | A | 1056 | inv-02.string_current_17_a | Ready |
| 57 | INVERTER_2.INV2_TOTAL_PRODUCTION | 212 | 2 | float32 | kWh | 1057 | inv-02.energy_total_kwh | Ready |
| 58 | INVERTER_3.INV3_AC_POWER | 214 | 2 | float32 | kW | 1058 | inv-03.ac_power_kw | Ready |
| 59 | INVERTER_3.INV3_CURRENT_I1 | 216 | 2 | float32 | A | 1059 | inv-03.current_l1_a | Ready |
| 60 | INVERTER_3.INV3_CURRENT_I2 | 218 | 2 | float32 | A | 1060 | inv-03.current_l2_a | Ready |
| 61 | INVERTER_3.INV3_CURRENT_I3 | 220 | 2 | float32 | A | 1061 | inv-03.current_l3_a | Ready |
| 62 | INVERTER_3.INV3_DC_POWER | 222 | 2 | float32 | kW | 1062 | inv-03.dc_power_kw | Ready |
| 63 | INVERTER_3.INV3_FREKANS | 224 | 2 | float32 | Hz | 1063 | inv-03.frequency_hz | Ready |
| 64 | INVERTER_3.INV3_VOLTAGE_V1 | 226 | 2 | float32 | V | 1064 | inv-03.voltage_l1_v | Ready |
| 65 | INVERTER_3.INV3_VOLTAGE_V2 | 228 | 2 | float32 | V | 1065 | inv-03.voltage_l2_v | Ready |
| 66 | INVERTER_3.INV3_VOLTAGE_V3 | 230 | 2 | float32 | V | 1066 | inv-03.voltage_l3_v | Ready |
| 67 | INVERTER_3.INV3_DAILY_PRODUCTION | 232 | 2 | float32 | kWh | 1067 | inv-03.energy_today_kwh | Ready |
| 68 | INVERTER_3.INV3_Connection_Error | 234 | 1 | boolean |  | 1068 | inv-03.comm_error | Ready |
| 69 | INVERTER_3.INV3_String_CURRENT_I1 | 235 | 2 | float32 | A | 1069 | inv-03.string_current_1_a | Ready |
| 70 | INVERTER_3.INV3_String_CURRENT_I2 | 237 | 2 | float32 | A | 1070 | inv-03.string_current_2_a | Ready |
| 71 | INVERTER_3.INV3_String_CURRENT_I3 | 239 | 2 | float32 | A | 1071 | inv-03.string_current_3_a | Ready |
| 72 | INVERTER_3.INV3_String_CURRENT_I4 | 241 | 2 | float32 | A | 1072 | inv-03.string_current_4_a | Ready |
| 73 | INVERTER_3.INV3_String_CURRENT_I5 | 243 | 2 | float32 | A | 1073 | inv-03.string_current_5_a | Ready |
| 74 | INVERTER_3.INV3_String_CURRENT_I6 | 245 | 2 | float32 | A | 1074 | inv-03.string_current_6_a | Ready |
| 75 | INVERTER_3.INV3_String_CURRENT_I7 | 247 | 2 | float32 | A | 1075 | inv-03.string_current_7_a | Ready |
| 76 | INVERTER_3.INV3_String_CURRENT_I8 | 249 | 2 | float32 | A | 1076 | inv-03.string_current_8_a | Ready |
| 77 | INVERTER_3.INV3_String_CURRENT_I9 | 251 | 2 | float32 | A | 1077 | inv-03.string_current_9_a | Ready |
| 78 | INVERTER_3.INV3_String_CURRENT_I10 | 253 | 2 | float32 | A | 1078 | inv-03.string_current_10_a | Ready |
| 79 | INVERTER_3.INV3_String_CURRENT_I11 | 255 | 2 | float32 | A | 1079 | inv-03.string_current_11_a | Ready |
| 80 | INVERTER_3.INV3_String_CURRENT_I12 | 257 | 2 | float32 | A | 1080 | inv-03.string_current_12_a | Ready |
| 81 | INVERTER_3.INV3_String_CURRENT_I13 | 259 | 2 | float32 | A | 1081 | inv-03.string_current_13_a | Ready |
| 82 | INVERTER_3.INV3_String_CURRENT_I14 | 261 | 2 | float32 | A | 1082 | inv-03.string_current_14_a | Ready |
| 83 | INVERTER_3.INV3_String_CURRENT_I15 | 263 | 2 | float32 | A | 1083 | inv-03.string_current_15_a | Ready |
| 84 | INVERTER_3.INV3_String_CURRENT_I16 | 265 | 2 | float32 | A | 1084 | inv-03.string_current_16_a | Ready |
| 85 | INVERTER_3.INV3_String_CURRENT_I17 | 267 | 2 | float32 | A | 1085 | inv-03.string_current_17_a | Ready |
| 86 | INVERTER_3.INV3_TOTAL_PRODUCTION | 269 | 2 | float32 | kWh | 1086 | inv-03.energy_total_kwh | Ready |
| 87 | INVERTER_4.INV4_AC_POWER | 271 | 2 | float32 | kW | 1087 | inv-04.ac_power_kw | Ready |
| 88 | INVERTER_4.INV4_CURRENT_I1 | 273 | 2 | float32 | A | 1088 | inv-04.current_l1_a | Ready |
| 89 | INVERTER_4.INV4_CURRENT_I2 | 275 | 2 | float32 | A | 1089 | inv-04.current_l2_a | Ready |
| 90 | INVERTER_4.INV4_CURRENT_I3 | 277 | 2 | float32 | A | 1090 | inv-04.current_l3_a | Ready |
| 91 | INVERTER_4.INV4_DC_POWER | 279 | 2 | float32 | kW | 1091 | inv-04.dc_power_kw | Ready |
| 92 | INVERTER_4.INV4_FREKANS | 281 | 2 | float32 | Hz | 1092 | inv-04.frequency_hz | Ready |
| 93 | INVERTER_4.INV4_VOLTAGE_V1 | 283 | 2 | float32 | V | 1093 | inv-04.voltage_l1_v | Ready |
| 94 | INVERTER_4.INV4_VOLTAGE_V2 | 285 | 2 | float32 | V | 1094 | inv-04.voltage_l2_v | Ready |
| 95 | INVERTER_4.INV4_VOLTAGE_V3 | 287 | 2 | float32 | V | 1095 | inv-04.voltage_l3_v | Ready |
| 96 | INVERTER_4.INV4_DAILY_PRODUCTION | 289 | 2 | float32 | kWh | 1096 | inv-04.energy_today_kwh | Ready |
| 97 | INVERTER_4.INV4_Connection_Error | 291 | 1 | boolean |  | 1097 | inv-04.comm_error | Ready |
| 98 | INVERTER_4.INV4_String_CURRENT_I1 | 292 | 2 | float32 | A | 1098 | inv-04.string_current_1_a | Ready |
| 99 | INVERTER_4.INV4_String_CURRENT_I2 | 294 | 2 | float32 | A | 1099 | inv-04.string_current_2_a | Ready |
| 100 | INVERTER_4.INV4_String_CURRENT_I3 | 296 | 2 | float32 | A | 1100 | inv-04.string_current_3_a | Ready |
| 101 | INVERTER_4.INV4_String_CURRENT_I4 | 298 | 2 | float32 | A | 1101 | inv-04.string_current_4_a | Ready |
| 102 | INVERTER_4.INV4_String_CURRENT_I5 | 300 | 2 | float32 | A | 1102 | inv-04.string_current_5_a | Ready |
| 103 | INVERTER_4.INV4_String_CURRENT_I6 | 302 | 2 | float32 | A | 1103 | inv-04.string_current_6_a | Ready |
| 104 | INVERTER_4.INV4_String_CURRENT_I7 | 304 | 2 | float32 | A | 1104 | inv-04.string_current_7_a | Ready |
| 105 | INVERTER_4.INV4_String_CURRENT_I8 | 306 | 2 | float32 | A | 1105 | inv-04.string_current_8_a | Ready |
| 106 | INVERTER_4.INV4_String_CURRENT_I9 | 308 | 2 | float32 | A | 1106 | inv-04.string_current_9_a | Ready |
| 107 | INVERTER_4.INV4_String_CURRENT_I10 | 310 | 2 | float32 | A | 1107 | inv-04.string_current_10_a | Ready |
| 108 | INVERTER_4.INV4_String_CURRENT_I11 | 312 | 2 | float32 | A | 1108 | inv-04.string_current_11_a | Ready |
| 109 | INVERTER_4.INV4_String_CURRENT_I12 | 314 | 2 | float32 | A | 1109 | inv-04.string_current_12_a | Ready |
| 110 | INVERTER_4.INV4_String_CURRENT_I13 | 316 | 2 | float32 | A | 1110 | inv-04.string_current_13_a | Ready |
| 111 | INVERTER_4.INV4_String_CURRENT_I14 | 318 | 2 | float32 | A | 1111 | inv-04.string_current_14_a | Ready |
| 112 | INVERTER_4.INV4_String_CURRENT_I15 | 320 | 2 | float32 | A | 1112 | inv-04.string_current_15_a | Ready |
| 113 | INVERTER_4.INV4_String_CURRENT_I16 | 322 | 2 | float32 | A | 1113 | inv-04.string_current_16_a | Ready |
| 114 | INVERTER_4.INV4_String_CURRENT_I17 | 324 | 2 | float32 | A | 1114 | inv-04.string_current_17_a | Ready |
| 115 | INVERTER_4.INV4_TOTAL_PRODUCTION | 326 | 2 | float32 | kWh | 1115 | inv-04.energy_total_kwh | Ready |
| 116 | INVERTER_5.INV5_AC_POWER | 328 | 2 | float32 | kW | 1116 | inv-05.ac_power_kw | Ready |
| 117 | INVERTER_5.INV5_CURRENT_I1 | 330 | 2 | float32 | A | 1117 | inv-05.current_l1_a | Ready |
| 118 | INVERTER_5.INV5_CURRENT_I2 | 332 | 2 | float32 | A | 1118 | inv-05.current_l2_a | Ready |
| 119 | INVERTER_5.INV5_CURRENT_I3 | 334 | 2 | float32 | A | 1119 | inv-05.current_l3_a | Ready |
| 120 | INVERTER_5.INV5_DC_POWER | 336 | 2 | float32 | kW | 1120 | inv-05.dc_power_kw | Ready |
| 121 | INVERTER_5.INV5_FREKANS | 338 | 2 | float32 | Hz | 1121 | inv-05.frequency_hz | Ready |
| 122 | INVERTER_5.INV5_VOLTAGE_V1 | 340 | 2 | float32 | V | 1122 | inv-05.voltage_l1_v | Ready |
| 123 | INVERTER_5.INV5_VOLTAGE_V2 | 342 | 2 | float32 | V | 1123 | inv-05.voltage_l2_v | Ready |
| 124 | INVERTER_5.INV5_VOLTAGE_V3 | 344 | 2 | float32 | V | 1124 | inv-05.voltage_l3_v | Ready |
| 125 | INVERTER_5.INV5_DAILY_PRODUCTION | 346 | 2 | float32 | kWh | 1125 | inv-05.energy_today_kwh | Ready |
| 126 | INVERTER_5.INV5_Connection_Error | 348 | 1 | boolean |  | 1126 | inv-05.comm_error | Ready |
| 127 | INVERTER_5.INV5_String_CURRENT_I1 | 349 | 2 | float32 | A | 1127 | inv-05.string_current_1_a | Ready |
| 128 | INVERTER_5.INV5_String_CURRENT_I2 | 351 | 2 | float32 | A | 1128 | inv-05.string_current_2_a | Ready |
| 129 | INVERTER_5.INV5_String_CURRENT_I3 | 353 | 2 | float32 | A | 1129 | inv-05.string_current_3_a | Ready |
| 130 | INVERTER_5.INV5_String_CURRENT_I4 | 355 | 2 | float32 | A | 1130 | inv-05.string_current_4_a | Ready |
| 131 | INVERTER_5.INV5_String_CURRENT_I5 | 357 | 2 | float32 | A | 1131 | inv-05.string_current_5_a | Ready |
| 132 | INVERTER_5.INV5_String_CURRENT_I6 | 359 | 2 | float32 | A | 1132 | inv-05.string_current_6_a | Ready |
| 133 | INVERTER_5.INV5_String_CURRENT_I7 | 361 | 2 | float32 | A | 1133 | inv-05.string_current_7_a | Ready |
| 134 | INVERTER_5.INV5_String_CURRENT_I8 | 363 | 2 | float32 | A | 1134 | inv-05.string_current_8_a | Ready |
| 135 | INVERTER_5.INV5_String_CURRENT_I9 | 365 | 2 | float32 | A | 1135 | inv-05.string_current_9_a | Ready |
| 136 | INVERTER_5.INV5_String_CURRENT_I10 | 367 | 2 | float32 | A | 1136 | inv-05.string_current_10_a | Ready |
| 137 | INVERTER_5.INV5_String_CURRENT_I11 | 369 | 2 | float32 | A | 1137 | inv-05.string_current_11_a | Ready |
| 138 | INVERTER_5.INV5_String_CURRENT_I12 | 371 | 2 | float32 | A | 1138 | inv-05.string_current_12_a | Ready |
| 139 | INVERTER_5.INV5_String_CURRENT_I13 | 373 | 2 | float32 | A | 1139 | inv-05.string_current_13_a | Ready |
| 140 | INVERTER_5.INV5_String_CURRENT_I14 | 375 | 2 | float32 | A | 1140 | inv-05.string_current_14_a | Ready |
| 141 | INVERTER_5.INV5_String_CURRENT_I15 | 377 | 2 | float32 | A | 1141 | inv-05.string_current_15_a | Ready |
| 142 | INVERTER_5.INV5_String_CURRENT_I16 | 379 | 2 | float32 | A | 1142 | inv-05.string_current_16_a | Ready |
| 143 | INVERTER_5.INV5_String_CURRENT_I17 | 381 | 2 | float32 | A | 1143 | inv-05.string_current_17_a | Ready |
| 144 | INVERTER_5.INV5_TOTAL_PRODUCTION | 383 | 2 | float32 | kWh | 1144 | inv-05.energy_total_kwh | Ready |
| 145 | INVERTER_6.INV6_AC_POWER | 385 | 2 | float32 | kW | 1145 | inv-06.ac_power_kw | Ready |
| 146 | INVERTER_6.INV6_CURRENT_I1 | 387 | 2 | float32 | A | 1146 | inv-06.current_l1_a | Ready |
| 147 | INVERTER_6.INV6_CURRENT_I2 | 389 | 2 | float32 | A | 1147 | inv-06.current_l2_a | Ready |
| 148 | INVERTER_6.INV6_CURRENT_I3 | 391 | 2 | float32 | A | 1148 | inv-06.current_l3_a | Ready |
| 149 | INVERTER_6.INV6_DC_POWER | 393 | 2 | float32 | kW | 1149 | inv-06.dc_power_kw | Ready |
| 150 | INVERTER_6.INV6_FREKANS | 395 | 2 | float32 | Hz | 1150 | inv-06.frequency_hz | Ready |
| 151 | INVERTER_6.INV6_VOLTAGE_V1 | 397 | 2 | float32 | V | 1151 | inv-06.voltage_l1_v | Ready |
| 152 | INVERTER_6.INV6_VOLTAGE_V2 | 399 | 2 | float32 | V | 1152 | inv-06.voltage_l2_v | Ready |
| 153 | INVERTER_6.INV6_VOLTAGE_V3 | 401 | 2 | float32 | V | 1153 | inv-06.voltage_l3_v | Ready |
| 154 | INVERTER_6.INV6_DAILY_PRODUCTION | 403 | 2 | float32 | kWh | 1154 | inv-06.energy_today_kwh | Ready |
| 155 | INVERTER_6.INV6_Connection_Error | 405 | 1 | boolean |  | 1155 | inv-06.comm_error | Ready |
| 156 | INVERTER_6.INV6_String_CURRENT_I1 | 406 | 2 | float32 | A | 1156 | inv-06.string_current_1_a | Ready |
| 157 | INVERTER_6.INV6_String_CURRENT_I2 | 408 | 2 | float32 | A | 1157 | inv-06.string_current_2_a | Ready |
| 158 | INVERTER_6.INV6_String_CURRENT_I3 | 410 | 2 | float32 | A | 1158 | inv-06.string_current_3_a | Ready |
| 159 | INVERTER_6.INV6_String_CURRENT_I4 | 412 | 2 | float32 | A | 1159 | inv-06.string_current_4_a | Ready |
| 160 | INVERTER_6.INV6_String_CURRENT_I5 | 414 | 2 | float32 | A | 1160 | inv-06.string_current_5_a | Ready |
| 161 | INVERTER_6.INV6_String_CURRENT_I6 | 416 | 2 | float32 | A | 1161 | inv-06.string_current_6_a | Ready |
| 162 | INVERTER_6.INV6_String_CURRENT_I7 | 418 | 2 | float32 | A | 1162 | inv-06.string_current_7_a | Ready |
| 163 | INVERTER_6.INV6_String_CURRENT_I8 | 420 | 2 | float32 | A | 1163 | inv-06.string_current_8_a | Ready |
| 164 | INVERTER_6.INV6_String_CURRENT_I9 | 422 | 2 | float32 | A | 1164 | inv-06.string_current_9_a | Ready |
| 165 | INVERTER_6.INV6_String_CURRENT_I10 | 424 | 2 | float32 | A | 1165 | inv-06.string_current_10_a | Ready |
| 166 | INVERTER_6.INV6_String_CURRENT_I11 | 426 | 2 | float32 | A | 1166 | inv-06.string_current_11_a | Ready |
| 167 | INVERTER_6.INV6_String_CURRENT_I12 | 428 | 2 | float32 | A | 1167 | inv-06.string_current_12_a | Ready |
| 168 | INVERTER_6.INV6_String_CURRENT_I13 | 430 | 2 | float32 | A | 1168 | inv-06.string_current_13_a | Ready |
| 169 | INVERTER_6.INV6_String_CURRENT_I14 | 432 | 2 | float32 | A | 1169 | inv-06.string_current_14_a | Ready |
| 170 | INVERTER_6.INV6_String_CURRENT_I15 | 434 | 2 | float32 | A | 1170 | inv-06.string_current_15_a | Ready |
| 171 | INVERTER_6.INV6_String_CURRENT_I16 | 436 | 2 | float32 | A | 1171 | inv-06.string_current_16_a | Ready |
| 172 | INVERTER_6.INV6_String_CURRENT_I17 | 438 | 2 | float32 | A | 1172 | inv-06.string_current_17_a | Ready |
| 173 | INVERTER_6.INV6_TOTAL_PRODUCTION | 440 | 2 | float32 | kWh | 1173 | inv-06.energy_total_kwh | Ready |
| 174 | INVERTER_7.INV7_AC_POWER | 442 | 2 | float32 | kW | 1174 | inv-07.ac_power_kw | Ready |
| 175 | INVERTER_7.INV7_CURRENT_I1 | 444 | 2 | float32 | A | 1175 | inv-07.current_l1_a | Ready |
| 176 | INVERTER_7.INV7_CURRENT_I2 | 446 | 2 | float32 | A | 1176 | inv-07.current_l2_a | Ready |
| 177 | INVERTER_7.INV7_CURRENT_I3 | 448 | 2 | float32 | A | 1177 | inv-07.current_l3_a | Ready |
| 178 | INVERTER_7.INV7_DC_POWER | 450 | 2 | float32 | kW | 1178 | inv-07.dc_power_kw | Ready |
| 179 | INVERTER_7.INV7_FREKANS | 452 | 2 | float32 | Hz | 1179 | inv-07.frequency_hz | Ready |
| 180 | INVERTER_7.INV7_VOLTAGE_V1 | 454 | 2 | float32 | V | 1180 | inv-07.voltage_l1_v | Ready |
| 181 | INVERTER_7.INV7_VOLTAGE_V2 | 456 | 2 | float32 | V | 1181 | inv-07.voltage_l2_v | Ready |
| 182 | INVERTER_7.INV7_VOLTAGE_V3 | 458 | 2 | float32 | V | 1182 | inv-07.voltage_l3_v | Ready |
| 183 | INVERTER_7.INV7_DAILY_PRODUCTION | 460 | 2 | float32 | kWh | 1183 | inv-07.energy_today_kwh | Ready |
| 184 | INVERTER_7.INV7_Connection_Error | 462 | 1 | boolean |  | 1184 | inv-07.comm_error | Ready |
| 185 | INVERTER_7.INV7_String_CURRENT_I1 | 463 | 2 | float32 | A | 1185 | inv-07.string_current_1_a | Ready |
| 186 | INVERTER_7.INV7_String_CURRENT_I2 | 465 | 2 | float32 | A | 1186 | inv-07.string_current_2_a | Ready |
| 187 | INVERTER_7.INV7_String_CURRENT_I3 | 467 | 2 | float32 | A | 1187 | inv-07.string_current_3_a | Ready |
| 188 | INVERTER_7.INV7_String_CURRENT_I4 | 469 | 2 | float32 | A | 1188 | inv-07.string_current_4_a | Ready |
| 189 | INVERTER_7.INV7_String_CURRENT_I5 | 471 | 2 | float32 | A | 1189 | inv-07.string_current_5_a | Ready |
| 190 | INVERTER_7.INV7_String_CURRENT_I6 | 473 | 2 | float32 | A | 1190 | inv-07.string_current_6_a | Ready |
| 191 | INVERTER_7.INV7_String_CURRENT_I7 | 475 | 2 | float32 | A | 1191 | inv-07.string_current_7_a | Ready |
| 192 | INVERTER_7.INV7_String_CURRENT_I8 | 477 | 2 | float32 | A | 1192 | inv-07.string_current_8_a | Ready |
| 193 | INVERTER_7.INV7_String_CURRENT_I9 | 479 | 2 | float32 | A | 1193 | inv-07.string_current_9_a | Ready |
| 194 | INVERTER_7.INV7_String_CURRENT_I10 | 481 | 2 | float32 | A | 1194 | inv-07.string_current_10_a | Ready |
| 195 | INVERTER_7.INV7_String_CURRENT_I11 | 483 | 2 | float32 | A | 1195 | inv-07.string_current_11_a | Ready |
| 196 | INVERTER_7.INV7_String_CURRENT_I12 | 485 | 2 | float32 | A | 1196 | inv-07.string_current_12_a | Ready |
| 197 | INVERTER_7.INV7_String_CURRENT_I13 | 487 | 2 | float32 | A | 1197 | inv-07.string_current_13_a | Ready |
| 198 | INVERTER_7.INV7_String_CURRENT_I14 | 489 | 2 | float32 | A | 1198 | inv-07.string_current_14_a | Ready |
| 199 | INVERTER_7.INV7_String_CURRENT_I15 | 491 | 2 | float32 | A | 1199 | inv-07.string_current_15_a | Ready |
| 200 | INVERTER_7.INV7_String_CURRENT_I16 | 493 | 2 | float32 | A | 1200 | inv-07.string_current_16_a | Ready |
| 201 | INVERTER_7.INV7_String_CURRENT_I17 | 495 | 2 | float32 | A | 1201 | inv-07.string_current_17_a | Ready |
| 202 | INVERTER_7.INV7_TOTAL_PRODUCTION | 497 | 2 | float32 | kWh | 1202 | inv-07.energy_total_kwh | Ready |
| 203 | INVERTER_8.INV8_AC_POWER | 499 | 2 | float32 | kW | 1203 | inv-08.ac_power_kw | Ready |
| 204 | INVERTER_8.INV8_CURRENT_I1 | 501 | 2 | float32 | A | 1204 | inv-08.current_l1_a | Ready |
| 205 | INVERTER_8.INV8_CURRENT_I2 | 503 | 2 | float32 | A | 1205 | inv-08.current_l2_a | Ready |
| 206 | INVERTER_8.INV8_CURRENT_I3 | 505 | 2 | float32 | A | 1206 | inv-08.current_l3_a | Ready |
| 207 | INVERTER_8.INV8_DC_POWER | 507 | 2 | float32 | kW | 1207 | inv-08.dc_power_kw | Ready |
| 208 | INVERTER_8.INV8_FREKANS | 509 | 2 | float32 | Hz | 1208 | inv-08.frequency_hz | Ready |
| 209 | INVERTER_8.INV8_VOLTAGE_V1 | 511 | 2 | float32 | V | 1209 | inv-08.voltage_l1_v | Ready |
| 210 | INVERTER_8.INV8_VOLTAGE_V2 | 513 | 2 | float32 | V | 1210 | inv-08.voltage_l2_v | Ready |
| 211 | INVERTER_8.INV8_VOLTAGE_V3 | 515 | 2 | float32 | V | 1211 | inv-08.voltage_l3_v | Ready |
| 212 | INVERTER_8.INV8_DAILY_PRODUCTION | 517 | 2 | float32 | kWh | 1212 | inv-08.energy_today_kwh | Ready |
| 213 | INVERTER_8.INV8_Connection_Error | 519 | 1 | boolean |  | 1213 | inv-08.comm_error | Ready |
| 214 | INVERTER_8.INV8_String_CURRENT_I1 | 520 | 2 | float32 | A | 1214 | inv-08.string_current_1_a | Ready |
| 215 | INVERTER_8.INV8_String_CURRENT_I2 | 522 | 2 | float32 | A | 1215 | inv-08.string_current_2_a | Ready |
| 216 | INVERTER_8.INV8_String_CURRENT_I3 | 524 | 2 | float32 | A | 1216 | inv-08.string_current_3_a | Ready |
| 217 | INVERTER_8.INV8_String_CURRENT_I4 | 526 | 2 | float32 | A | 1217 | inv-08.string_current_4_a | Ready |
| 218 | INVERTER_8.INV8_String_CURRENT_I5 | 528 | 2 | float32 | A | 1218 | inv-08.string_current_5_a | Ready |
| 219 | INVERTER_8.INV8_String_CURRENT_I6 | 530 | 2 | float32 | A | 1219 | inv-08.string_current_6_a | Ready |
| 220 | INVERTER_8.INV8_String_CURRENT_I7 | 532 | 2 | float32 | A | 1220 | inv-08.string_current_7_a | Ready |
| 221 | INVERTER_8.INV8_String_CURRENT_I8 | 534 | 2 | float32 | A | 1221 | inv-08.string_current_8_a | Ready |
| 222 | INVERTER_8.INV8_String_CURRENT_I9 | 536 | 2 | float32 | A | 1222 | inv-08.string_current_9_a | Ready |
| 223 | INVERTER_8.INV8_String_CURRENT_I10 | 538 | 2 | float32 | A | 1223 | inv-08.string_current_10_a | Ready |
| 224 | INVERTER_8.INV8_String_CURRENT_I11 | 540 | 2 | float32 | A | 1224 | inv-08.string_current_11_a | Ready |
| 225 | INVERTER_8.INV8_String_CURRENT_I12 | 542 | 2 | float32 | A | 1225 | inv-08.string_current_12_a | Ready |
| 226 | INVERTER_8.INV8_String_CURRENT_I13 | 544 | 2 | float32 | A | 1226 | inv-08.string_current_13_a | Ready |
| 227 | INVERTER_8.INV8_String_CURRENT_I14 | 546 | 2 | float32 | A | 1227 | inv-08.string_current_14_a | Ready |
| 228 | INVERTER_8.INV8_String_CURRENT_I15 | 548 | 2 | float32 | A | 1228 | inv-08.string_current_15_a | Ready |
| 229 | INVERTER_8.INV8_String_CURRENT_I16 | 550 | 2 | float32 | A | 1229 | inv-08.string_current_16_a | Ready |
| 230 | INVERTER_8.INV8_String_CURRENT_I17 | 552 | 2 | float32 | A | 1230 | inv-08.string_current_17_a | Ready |
| 231 | INVERTER_8.INV8_TOTAL_PRODUCTION | 554 | 2 | float32 | kWh | 1231 | inv-08.energy_total_kwh | Ready |
| 232 | INVERTER_9.INV9_AC_POWER | 556 | 2 | float32 | kW | 1232 | inv-09.ac_power_kw | Ready |
| 233 | INVERTER_9.INV9_CURRENT_I1 | 558 | 2 | float32 | A | 1233 | inv-09.current_l1_a | Ready |
| 234 | INVERTER_9.INV9_CURRENT_I2 | 560 | 2 | float32 | A | 1234 | inv-09.current_l2_a | Ready |
| 235 | INVERTER_9.INV9_CURRENT_I3 | 562 | 2 | float32 | A | 1235 | inv-09.current_l3_a | Ready |
| 236 | INVERTER_9.INV9_DC_POWER | 564 | 2 | float32 | kW | 1236 | inv-09.dc_power_kw | Ready |
| 237 | INVERTER_9.INV9_FREKANS | 566 | 2 | float32 | Hz | 1237 | inv-09.frequency_hz | Ready |
| 238 | INVERTER_9.INV9_VOLTAGE_V1 | 568 | 2 | float32 | V | 1238 | inv-09.voltage_l1_v | Ready |
| 239 | INVERTER_9.INV9_VOLTAGE_V2 | 570 | 2 | float32 | V | 1239 | inv-09.voltage_l2_v | Ready |
| 240 | INVERTER_9.INV9_VOLTAGE_V3 | 572 | 2 | float32 | V | 1240 | inv-09.voltage_l3_v | Ready |
| 241 | INVERTER_9.INV9_DAILY_PRODUCTION | 574 | 2 | float32 | kWh | 1241 | inv-09.energy_today_kwh | Ready |
| 242 | INVERTER_9.INV9_Connection_Error | 576 | 1 | boolean |  | 1242 | inv-09.comm_error | Ready |
| 243 | INVERTER_9.INV9_String_CURRENT_I1 | 577 | 2 | float32 | A | 1243 | inv-09.string_current_1_a | Ready |
| 244 | INVERTER_9.INV9_String_CURRENT_I2 | 579 | 2 | float32 | A | 1244 | inv-09.string_current_2_a | Ready |
| 245 | INVERTER_9.INV9_String_CURRENT_I3 | 581 | 2 | float32 | A | 1245 | inv-09.string_current_3_a | Ready |
| 246 | INVERTER_9.INV9_String_CURRENT_I4 | 583 | 2 | float32 | A | 1246 | inv-09.string_current_4_a | Ready |
| 247 | INVERTER_9.INV9_String_CURRENT_I5 | 585 | 2 | float32 | A | 1247 | inv-09.string_current_5_a | Ready |
| 248 | INVERTER_9.INV9_String_CURRENT_I6 | 587 | 2 | float32 | A | 1248 | inv-09.string_current_6_a | Ready |
| 249 | INVERTER_9.INV9_String_CURRENT_I7 | 589 | 2 | float32 | A | 1249 | inv-09.string_current_7_a | Ready |
| 250 | INVERTER_9.INV9_String_CURRENT_I8 | 591 | 2 | float32 | A | 1250 | inv-09.string_current_8_a | Ready |
| 251 | INVERTER_9.INV9_String_CURRENT_I9 | 593 | 2 | float32 | A | 1251 | inv-09.string_current_9_a | Ready |
| 252 | INVERTER_9.INV9_String_CURRENT_I10 | 595 | 2 | float32 | A | 1252 | inv-09.string_current_10_a | Ready |
| 253 | INVERTER_9.INV9_String_CURRENT_I11 | 597 | 2 | float32 | A | 1253 | inv-09.string_current_11_a | Ready |
| 254 | INVERTER_9.INV9_String_CURRENT_I12 | 599 | 2 | float32 | A | 1254 | inv-09.string_current_12_a | Ready |
| 255 | INVERTER_9.INV9_String_CURRENT_I13 | 601 | 2 | float32 | A | 1255 | inv-09.string_current_13_a | Ready |
| 256 | INVERTER_9.INV9_String_CURRENT_I14 | 603 | 2 | float32 | A | 1256 | inv-09.string_current_14_a | Ready |
| 257 | INVERTER_9.INV9_String_CURRENT_I15 | 605 | 2 | float32 | A | 1257 | inv-09.string_current_15_a | Ready |
| 258 | INVERTER_9.INV9_String_CURRENT_I16 | 607 | 2 | float32 | A | 1258 | inv-09.string_current_16_a | Ready |
| 259 | INVERTER_9.INV9_String_CURRENT_I17 | 609 | 2 | float32 | A | 1259 | inv-09.string_current_17_a | Ready |
| 260 | INVERTER_9.INV9_TOTAL_PRODUCTION | 611 | 2 | float32 | kWh | 1260 | inv-09.energy_total_kwh | Ready |
| 261 | INVERTER_10.INV10_AC_POWER | 613 | 2 | float32 | kW | 1261 | inv-10.ac_power_kw | Ready |
| 262 | INVERTER_10.INV10_CURRENT_I1 | 615 | 2 | float32 | A | 1262 | inv-10.current_l1_a | Ready |
| 263 | INVERTER_10.INV10_CURRENT_I2 | 617 | 2 | float32 | A | 1263 | inv-10.current_l2_a | Ready |
| 264 | INVERTER_10.INV10_CURRENT_I3 | 619 | 2 | float32 | A | 1264 | inv-10.current_l3_a | Ready |
| 265 | INVERTER_10.INV10_DC_POWER | 621 | 2 | float32 | kW | 1265 | inv-10.dc_power_kw | Ready |
| 266 | INVERTER_10.INV10_FREKANS | 623 | 2 | float32 | Hz | 1266 | inv-10.frequency_hz | Ready |
| 267 | INVERTER_10.INV10_VOLTAGE_V1 | 625 | 2 | float32 | V | 1267 | inv-10.voltage_l1_v | Ready |
| 268 | INVERTER_10.INV10_VOLTAGE_V2 | 627 | 2 | float32 | V | 1268 | inv-10.voltage_l2_v | Ready |
| 269 | INVERTER_10.INV10_VOLTAGE_V3 | 629 | 2 | float32 | V | 1269 | inv-10.voltage_l3_v | Ready |
| 270 | INVERTER_10.INV10_DAILY_PRODUCTION | 631 | 2 | float32 | kWh | 1270 | inv-10.energy_today_kwh | Ready |
| 271 | INVERTER_10.INV10_Connection_Error | 633 | 1 | boolean |  | 1271 | inv-10.comm_error | Ready |
| 272 | INVERTER_10.INV10_String_CURRENT_I1 | 634 | 2 | float32 | A | 1272 | inv-10.string_current_1_a | Ready |
| 273 | INVERTER_10.INV10_String_CURRENT_I2 | 636 | 2 | float32 | A | 1273 | inv-10.string_current_2_a | Ready |
| 274 | INVERTER_10.INV10_String_CURRENT_I3 | 638 | 2 | float32 | A | 1274 | inv-10.string_current_3_a | Ready |
| 275 | INVERTER_10.INV10_String_CURRENT_I4 | 640 | 2 | float32 | A | 1275 | inv-10.string_current_4_a | Ready |
| 276 | INVERTER_10.INV10_String_CURRENT_I5 | 642 | 2 | float32 | A | 1276 | inv-10.string_current_5_a | Ready |
| 277 | INVERTER_10.INV10_String_CURRENT_I6 | 644 | 2 | float32 | A | 1277 | inv-10.string_current_6_a | Ready |
| 278 | INVERTER_10.INV10_String_CURRENT_I7 | 646 | 2 | float32 | A | 1278 | inv-10.string_current_7_a | Ready |
| 279 | INVERTER_10.INV10_String_CURRENT_I8 | 648 | 2 | float32 | A | 1279 | inv-10.string_current_8_a | Ready |
| 280 | INVERTER_10.INV10_String_CURRENT_I9 | 650 | 2 | float32 | A | 1280 | inv-10.string_current_9_a | Ready |
| 281 | INVERTER_10.INV10_String_CURRENT_I10 | 652 | 2 | float32 | A | 1281 | inv-10.string_current_10_a | Ready |
| 282 | INVERTER_10.INV10_String_CURRENT_I11 | 654 | 2 | float32 | A | 1282 | inv-10.string_current_11_a | Ready |
| 283 | INVERTER_10.INV10_String_CURRENT_I12 | 656 | 2 | float32 | A | 1283 | inv-10.string_current_12_a | Ready |
| 284 | INVERTER_10.INV10_String_CURRENT_I13 | 658 | 2 | float32 | A | 1284 | inv-10.string_current_13_a | Ready |
| 285 | INVERTER_10.INV10_String_CURRENT_I14 | 660 | 2 | float32 | A | 1285 | inv-10.string_current_14_a | Ready |
| 286 | INVERTER_10.INV10_String_CURRENT_I15 | 662 | 2 | float32 | A | 1286 | inv-10.string_current_15_a | Ready |
| 287 | INVERTER_10.INV10_String_CURRENT_I16 | 664 | 2 | float32 | A | 1287 | inv-10.string_current_16_a | Ready |
| 288 | INVERTER_10.INV10_String_CURRENT_I17 | 666 | 2 | float32 | A | 1288 | inv-10.string_current_17_a | Ready |
| 289 | INVERTER_10.INV10_TOTAL_PRODUCTION | 668 | 2 | float32 | kWh | 1289 | inv-10.energy_total_kwh | Ready |
| 290 | Apparent current L1 N | 670 | 2 | float32 | A | 1290 | pv-meter.current_l1_a | Pending commissioning |
| 291 | Apparent current L2 N | 672 | 2 | float32 | A | 1291 | pv-meter.current_l2_a | Pending commissioning |
| 292 | Apparent current L3 N | 674 | 2 | float32 | A | 1292 | pv-meter.current_l3_a | Pending commissioning |
| 293 | Psum3 | 676 | 2 | float32 | kW | 1293 | neo-01.pv_prod_kw | Ready |
| 294 | Qsum3 | 678 | 2 | float32 | kvar | 1294 | neo-01.pv_q_kvar | Ready |
| 295 | Real energy L1 L3 delivered | 680 | 2 | float32 | kWh | 1295 | pv-array.energy_total_kwh | Ready |
| 296 | Voltage L1 N | 682 | 2 | float32 | V | 1296 | neo-01.bus_voltage_l1_v | Ready |
| 297 | Voltage L2 N | 684 | 2 | float32 | V | 1297 | neo-01.bus_voltage_l2_v | Ready |
| 298 | Voltage L3 N | 686 | 2 | float32 | V | 1298 | neo-01.bus_voltage_l3_v | Ready |
| 299 | Panel Temperature | 688 | 2 | float32 | °C | 1299 | pv-env.panel_temp_c | Pending commissioning |
| 300 | Pyranometer | 690 | 2 | float32 | W/m² | 1300 | pv-env.irradiance_wm2 | Pending commissioning |
| 301 | Area_Connection_Error | 692 | 1 | boolean |  | 1301 | neo-01.pv_logger_comm_fail | Ready |
| 302 | Thermal_Magnetic_Switch_On_(LV PANEL) | 693 | 1 | boolean |  | 1302 | neo-01.pvcb_closed | Ready |
| 303 | Thermal_Magnetic_Switch_Off (LV PANEL) | 694 | 1 | boolean |  | 1303 | neo-01.pvcb_open | Ready |
| 304 | Total_Energy | 695 | 2 | float32 | kWh | 1304 | pv-array.energy_total_kwh | Ready |

Value block: 100–696. Quality block: 1000–1304.

## Analyzer block (map version 2)

The plant's power analyzers (Schneider PM800 meters on the LV feeders), read every 15 s by the collector; the feed shows the latest reading. One **fixed slot per analyzer number** *n* (1 to 48): analyzer 7 is slot 7 whether or not 3 to 6 exist, so a meter added or removed on the dashboard never moves another one's registers. An empty slot reads 0 in every register, quality 0.

- Value address = **2000 + 32 × (n − 1) + offset**
- Quality address = **4000 + 16 × (n − 1) + index**
- Values are float32, 2 registers, high word first (ABCD), engineering units — except `E_IN_KWH`, the meter's own energy counter: **uint32**, 2 registers, high word first, whole kWh.
- Quality as above: 1 good, 2 stale, 0 bad / no data / empty slot.
- Tag names: `ANALYZER_<n>.<POINT>`, for example `ANALYZER_7.P_TOTAL_KW`.
- Header register 7 says how many analyzers are published, register 8 how many slots exist.

| Index | Offset | Point | Type | Unit | Meaning |
|---|---|---|---|---|---|
| 0 | 0 | P_TOTAL_KW | float32 | kW | Active power, total (+ into the load) |
| 1 | 2 | Q_TOTAL_KVAR | float32 | kvar | Reactive power, total |
| 2 | 4 | S_TOTAL_KVA | float32 | kVA | Apparent power, total |
| 3 | 6 | PF_TOTAL | float32 |  | Power factor, total |
| 4 | 8 | I_L1_A | float32 | A | Current L1 |
| 5 | 10 | I_L2_A | float32 | A | Current L2 |
| 6 | 12 | I_L3_A | float32 | A | Current L3 |
| 7 | 14 | I_N_A | float32 | A | Current neutral |
| 8 | 16 | V_L1L2_V | float32 | V | Voltage L1-L2 |
| 9 | 18 | V_L2L3_V | float32 | V | Voltage L2-L3 |
| 10 | 20 | V_L3L1_V | float32 | V | Voltage L3-L1 |
| 11 | 22 | V_L1N_V | float32 | V | Voltage L1-N |
| 12 | 24 | V_L2N_V | float32 | V | Voltage L2-N |
| 13 | 26 | V_L3N_V | float32 | V | Voltage L3-N |
| 14 | 28 | F_HZ | float32 | Hz | Frequency |
| 15 | 30 | E_IN_KWH | uint32 | kWh | Active energy into the load, the meter's counter (whole kWh) |

Example, analyzer 7: `P_TOTAL_KW` at 2192–2193 (holding register 42193), `E_IN_KWH` at 2222–2223, quality of `P_TOTAL_KW` at 4096, of `E_IN_KWH` at 4111.

Which analyzers exist on the plant right now, with their names and addresses filled in, comes from the collector itself: `http://<plant-pc-ip>:8080/api/v1/tags/analyzers.xlsx` (also `.csv`, and live values as JSON at `/api/v1/tags/analyzers`). The full map, solar and analyzers, is at `/api/v1/tags/registers`.

Value block (analyzers): 2000–3535. Quality block (analyzers): 4000–4767. Registers 0–8 and 100–1304 as above; everything else reads 0.

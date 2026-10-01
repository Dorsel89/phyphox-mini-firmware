"""Machine readable description of the phyphox:mini BLE interface.

Single place where UUIDs, config byte layouts and frame layouts live, so the
individual test scripts stay readable and there is only one thing to update
when the firmware changes. Mirrors phyfob-interface.yaml.
"""


def uuid(short):
    """cddfXXXX-30f7-4671-8b43-5e40ba53514a"""
    return f"cddf{short:04x}-30f7-4671-8b43-5e40ba53514a"


def sig_uuid(short):
    """Bluetooth SIG 16 bit UUID in its 128 bit form."""
    return f"0000{short:04x}-0000-1000-8000-00805f9b34fb"


# --- characteristics -------------------------------------------------------
RUN_CONTROL = uuid(0x0004)

BMP_DATA, BMP_CFG = uuid(0x1007), uuid(0x1008)
GYR_DATA, ACC_DATA, LSM_CFG = uuid(0x1000), uuid(0x1001), uuid(0x1002)
HDC_DATA, HDC_CFG = uuid(0x1005), uuid(0x1006)
STCC4_DATA, STCC4_CFG = uuid(0x100B), uuid(0x100C)
DATALOG_DATA, DATALOG_CFG = uuid(0x1010), uuid(0x1011)
DEVICE_CFG = uuid(0x1022)

DIS_MODEL = sig_uuid(0x2A24)
DIS_FIRMWARE = sig_uuid(0x2A26)
DIS_MANUFACTURER = sig_uuid(0x2A29)
BATTERY_LEVEL = sig_uuid(0x2A19)

ALL_PHYFOB_CHARS = [
    RUN_CONTROL, BMP_DATA, BMP_CFG, GYR_DATA, ACC_DATA, LSM_CFG,
    HDC_DATA, HDC_CFG, STCC4_DATA, STCC4_CFG, DATALOG_DATA, DATALOG_CFG,
    DEVICE_CFG,
]

# --- connection parameters (cddf1022, command 0x01) ------------------------
DEVICE_CMD_CONN_PARAMS = 0x01


def conn_params(interval_min_ms=30.0, interval_max_ms=30.0, latency=0,
                timeout_ms=2500):
    """Payload asking the fob for new connection parameters.

    The firmware keeps every field in a single byte: intervals in 1.25 ms
    steps, the supervision timeout in 10 ms steps - so at most 2550 ms.
    Without such a request the fob asks for Zephyr's defaults five seconds
    after connecting, 30-50 ms with a supervision timeout of only 420 ms.
    """
    fields = {
        "interval_min": round(interval_min_ms / 1.25),
        "interval_max": round(interval_max_ms / 1.25),
        "latency": int(latency),
        "timeout": round(timeout_ms / 10),
    }
    for name, value in fields.items():
        if not 0 <= value <= 255:
            raise ValueError(f"{name}={value} passt nicht in ein Byte")
    return bytes([DEVICE_CMD_CONN_PARAMS, fields["interval_min"],
                  fields["interval_max"], fields["latency"], fields["timeout"]])


# --- run control -----------------------------------------------------------
RUN_START = 0x01     # resets the time reference and opens the notification gate
RUN_CLEAR = 0x02     # closes it again and arms the next start

# --- LSM6DSR ---------------------------------------------------------------
LSM_ACC_BIT, LSM_GYR_BIT = 0x01, 0x02
LSM_FORMAT_FLOAT, LSM_FORMAT_INT16 = 0x00, 0x01

# rate byte -> nominal ODR in Hz
LSM_RATES = {0x01: 12.5, 0x02: 26, 0x03: 52, 0x04: 104, 0x05: 208,
             0x06: 416, 0x07: 833, 0x08: 1666, 0x09: 3332, 0x0A: 6667}

# range byte -> full scale, and the resulting scale factor per raw LSB
LSM_ACC_RANGES = {0x00: 2, 0x02: 4, 0x03: 8, 0x01: 16}          # g
LSM_GYR_RANGES = {0x02: 125, 0x00: 250, 0x04: 500,
                  0x08: 1000, 0x0C: 2000, 0x01: 4000}           # deg/s

# datasheet sensitivities, mg/LSB and mdps/LSB
LSM_ACC_MG_PER_LSB = {0x00: 0.061, 0x02: 0.122, 0x03: 0.244, 0x01: 0.488}
LSM_GYR_MDPS_PER_LSB = {0x02: 4.375, 0x00: 8.75, 0x04: 17.5,
                        0x08: 35.0, 0x0C: 70.0, 0x01: 140.0}

LSM_FLOAT_MAX_EVENT_SIZE = 15    # packet buffer holds 15 frames
LSM_INT16_MAX_EVENT_SIZE = 40    # 4 + 6*40 = 244 bytes, the ATT payload limit


def lsm_config(enable, rate, range_acc=0x01, range_gyr=0x02,
               fmt=LSM_FORMAT_FLOAT, event_size=15):
    """The seven config bytes of cddf1002."""
    return bytes([enable, rate, range_acc, range_gyr, fmt, event_size, 0x00])


# --- BMP581 ----------------------------------------------------------------
BMP_OFF, BMP_ON = 0x00, 0x01
BMP_OVERSAMPLING = {0x00: 1, 0x01: 2, 0x02: 4, 0x03: 8,
                    0x04: 16, 0x05: 32, 0x06: 64, 0x07: 128}
BMP_IIR = {0x00: 0, 0x01: 1, 0x02: 3, 0x03: 7,
           0x04: 15, 0x05: 31, 0x06: 63, 0x07: 127}
# how many 12 byte frames the firmware packs per notification, by oversampling
BMP_FRAMES_PER_PACKET = {0x00: 10, 0x01: 6, 0x02: 4, 0x03: 3,
                         0x04: 2, 0x05: 1, 0x06: 1, 0x07: 1}


def bmp_config(enable, oversampling=0x00, iir=0x01):
    return bytes([enable, oversampling, iir])


# --- HDC1080 and STCC4 -----------------------------------------------------
def interval_config(enable, interval_ms, minimum_ms):
    """Both sensors take the interval as a multiple of 100 ms in byte 1."""
    steps = max(1, round(interval_ms / 100))
    if steps > 255:
        raise ValueError(f"{interval_ms} ms exceeds the 25500 ms the byte can hold")
    effective = max(steps * 100, minimum_ms)
    return bytes([enable, steps]), effective


HDC_MIN_INTERVAL_MS = 300
STCC4_MIN_INTERVAL_MS = 1000
STCC4_CALIBRATION = 0x02

# --- datalog ---------------------------------------------------------------
DL_STOP, DL_START, DL_DUMP, DL_ERASE, DL_BTHOME = 0x00, 0x01, 0x02, 0x03, 0x04
DL_CO2, DL_TEMP, DL_HUMIDITY, DL_PRESSURE = 0x01, 0x02, 0x04, 0x08
DL_ALL = DL_CO2 | DL_TEMP | DL_HUMIDITY | DL_PRESSURE
DL_FIELDS = [(DL_CO2, "co2"), (DL_TEMP, "temperature"),
             (DL_HUMIDITY, "humidity"), (DL_PRESSURE, "pressure")]


def datalog_command(cmd, mask=0, param=0):
    return bytes([cmd, mask, param & 0xFF, (param >> 8) & 0xFF])


def datalog_record_size(mask):
    return 4 + 4 * bin(mask & 0x0F).count("1")


# --- live stream frame layouts ---------------------------------------------
# name, struct format of one frame, field names, frame size
FRAMES = {
    "bmp581":  ("<fff",  ["pressure", "temperature", "t"], 12),
    "hdc1080": ("<fff",  ["temperature", "humidity", "t"], 12),
    "stcc4":   ("<ff",   ["co2", "t"], 8),
    "lsm_float": ("<ffff", ["t", "x", "y", "z"], 16),
}

# plausible ranges for a device sitting on a desk, used for sanity checks
PLAUSIBLE = {
    "pressure": (800.0, 1100.0),        # hPa
    "temperature": (-10.0, 60.0),       # degC
    "humidity": (0.0, 100.0),           # %RH
    "co2": (250.0, 10000.0),            # ppm
    "battery": (0.0, 100.0),            # %
}

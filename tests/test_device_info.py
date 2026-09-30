#!/usr/bin/env python3
"""Device information, battery and GATT structure of a phyphox:mini.

    python test_device_info.py --address E8:C6:21:E2:29:78
"""
import sys

from minitest import spec
from minitest.runner import standalone


async def run(dev, rep, args):
    rep.section("Name und GATT-Struktur")
    rep.check("Geraetename beginnt mit 'phyphox:mini'",
              dev.name.startswith("phyphox:mini"), repr(dev.name))
    rep.check("MTU reicht fuer 244-Byte-Notifications",
              dev.client.mtu_size >= 247, f"MTU {dev.client.mtu_size}")

    missing = [c for c in spec.ALL_PHYFOB_CHARS if not dev.has_char(c)]
    rep.check("alle phyphox:mini-Characteristics vorhanden",
              not missing, f"fehlen: {[m[:8] for m in missing]}" if missing else "13 von 13")

    rep.section("Device Information Service")
    for title, char in (("Hersteller", spec.DIS_MANUFACTURER),
                        ("Modell", spec.DIS_MODEL),
                        ("Firmware-Version", spec.DIS_FIRMWARE)):
        if not dev.has_char(char):
            rep.skip(title, "Characteristic nicht vorhanden")
            continue
        try:
            value = await dev.read_string(char)
            rep.check(title, bool(value), repr(value))
        except Exception as exc:                  # noqa: BLE001
            rep.check(title, False, f"{type(exc).__name__}: {exc}")

    rep.section("Batterie")
    if not dev.has_char(spec.BATTERY_LEVEL):
        rep.skip("Ladezustand", "Battery Service nicht vorhanden")
        return
    raw = await dev.read(spec.BATTERY_LEVEL)
    if len(raw) != 1:
        rep.check("Ladezustand ist ein Byte", False, f"{len(raw)} Byte: {raw.hex()}")
        return
    lo, hi = spec.PLAUSIBLE["battery"]
    rep.metric("device.battery_pct", raw[0], unit="%", label="Ladezustand")
    rep.within("Ladezustand plausibel", raw[0], lo, hi, unit=" %")


if __name__ == "__main__":
    sys.exit(standalone(run, "device-info"))

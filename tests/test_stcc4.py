#!/usr/bin/env python3
"""STCC4 (CO2) of a phyphox:mini.

Like the HDC the interval sits in byte 1 in steps of 100 ms, here with a
minimum of 1000 ms (one result per second). The forced recalibration
(enable = 0x02) changes the sensor calibration permanently, so no test ever
sends it - calibrating is left to STCC4_Calibration.phyphox.

    python test_stcc4.py --address E8:C6:21:E2:29:78
"""
import statistics
import sys

from minitest import spec
from minitest.runner import standalone

FMT, NAMES, SIZE = spec.FRAMES["stcc4"]

# Lowest value the sensor reports; below that its output is clipped
CO2_FLOOR_PPM = 380.0


def extra(p):
    p.add_argument("--intervals", default="1000,2000",
                   help="zu pruefende Intervalle in ms, kommagetrennt")
    p.add_argument("--cycles", type=int, default=8,
                   help="wie viele Messwerte je Intervall abgewartet werden")
    p.add_argument("--warmup", type=float, default=6.0,
                   help="Vorlauf in Sekunden - die ersten Messungen sind oft 0 ppm")


async def run(dev, rep, args):
    intervals = [int(x) for x in args.intervals.split(",") if x.strip()]

    async with dev.stream(spec.STCC4_DATA) as stream:
        await dev.restart_run()

        rep.section("Grundfunktion")
        cfg, effective = spec.interval_config(0x01, 500, spec.STCC4_MIN_INTERVAL_MS)
        await dev.write(spec.STCC4_CFG, cfg)
        cap = await stream.capture(max(4.0, args.cycles * effective / 1000),
                                   settle=args.warmup)
        frames = cap.frames(FMT, NAMES, SIZE)

        if not rep.check("Daten kommen an", len(frames) > 0,
                         f"{len(cap)} Notifications, {len(frames)} Messwerte"):
            await dev.write(spec.STCC4_CFG, bytes([0x00, 0x05]))
            return

        bad = [n for n in cap.lengths if n % SIZE]
        rep.check(f"Paketlaengen sind Vielfache von {SIZE} Byte",
                  not bad, f"Laengen {dict(cap.lengths)}")

        co2 = [f["co2"] for f in frames]
        rep.raw("stcc4.basic", {"CO2": (co2, "ppm")}, t=[f["t"] for f in frames],
                label="CO2-Messwerte im Grundtest")
        zeros = sum(1 for c in co2 if c == 0.0)
        rep.metric("stcc4.zeros_fraction", zeros / len(co2),
                   label="Anteil Nullwerte")
        good = [c for c in co2 if c != 0.0]
        if good:
            rep.metric("stcc4.co2_mean_ppm", statistics.mean(good), unit="ppm",
                       label="CO2 Mittelwert")
            rep.metric("stcc4.co2_noise_ppm", statistics.pstdev(good), unit="ppm",
                       label="CO2 Rauschen (stdev)")
            rep.metric("stcc4.co2_distinct", len(set(good)),
                       label="verschiedene CO2-Werte")
        if zeros == len(co2):
            rep.check("Sensor liefert Messwerte", False,
                      "durchgehend 0 ppm. Die Firmware prueft weder den Rueckgabewert "
                      "noch das Statuswort des STCC4, eine 0 kann also eine "
                      "fehlgeschlagene I2C-Lesung sein.")
        else:
            rep.check("Sensor liefert Messwerte", True,
                      f"{len(co2) - zeros} von {len(co2)} Werten ungleich 0")
            good = [c for c in co2 if c != 0.0]
            lo, hi = spec.PLAUSIBLE["co2"]
            rep.within("CO2 plausibel", statistics.mean(good), lo, hi, unit=" ppm")
            distinct = len(set(good))
            detail = (f"{distinct} verschiedene Werte, Spanne "
                      f"{min(good):.0f}..{max(good):.0f} ppm")
            if distinct == 1 and good[0] == CO2_FLOOR_PPM:
                # Measured on A19: breath reads up to 25000 ppm, room air sits
                # flat at 380. The sensor works but reads low and is clipped.
                detail += (f" - das ist die Untergrenze der Sensorausgabe. Der "
                           f"Sensor misst zu niedrig und sollte kalibriert werden "
                           f"(STCC4_Calibration.phyphox, Frischluft ~420 ppm). "
                           f"Anhauchen muss den Wert deutlich steigen lassen.")
            rep.check("CO2 rauscht, ist also kein eingefrorener Wert",
                      distinct > 1, detail)
            if zeros:
                rep.warn("einzelne Nullwerte", f"{zeros} von {len(co2)}")

        st = cap.timestamp_stats(frames)
        rep.check("Zeitstempel streng aufsteigend", st and st["non_monotonic"] == 0,
                  f"{st['non_monotonic']} Rueckspruenge" if st else "")

        rep.section("Konfiguration: Intervall")
        for want_ms in intervals:
            cfg, effective = spec.interval_config(0x01, want_ms, spec.STCC4_MIN_INTERVAL_MS)
            await dev.write(spec.STCC4_CFG, cfg)
            window = max(4.0, args.cycles * effective / 1000)
            cap = await stream.capture(window, settle=effective / 1000 + 0.5)
            st = cap.timestamp_stats(cap.frames(FMT, NAMES, SIZE))
            note = "" if want_ms == effective else f" (auf {effective} ms angehoben)"
            if not st or st["median_dt_s"] is None:
                rep.check(f"Intervall {want_ms} ms{note}", False,
                          f"zu wenige Messwerte in {window:.1f}s")
                continue
            rep.near(f"Intervall {want_ms} ms{note}",
                     st["median_dt_s"] * 1000, effective,
                     tol_rel=0.25, tol_abs=60, unit=" ms")

        rep.section("Abschalten")
        await dev.write(spec.STCC4_CFG, bytes([0x00, 0x05]))
        cap = await stream.capture(2.0, settle=1.0)
        rep.check("nach enable=0 kommen keine Daten mehr", len(cap) == 0,
                  f"{len(cap)} Notifications trotzdem erhalten")

        rep.section("Kalibrierung")
        rep.skip("Forced Recalibration",
                 "veraendert die Sensorkalibrierung dauerhaft, daher nicht "
                 "Teil des automatischen Laufs")


if __name__ == "__main__":
    sys.exit(standalone(run, "stcc4", extra))

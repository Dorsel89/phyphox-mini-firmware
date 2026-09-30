#!/usr/bin/env python3
"""HDC1080 (temperature, humidity) of a phyphox:mini.

The interval is byte 1 of the config, in steps of 100 ms with a minimum of
300 ms. The test sets several intervals and checks that the measured spacing
of the timestamps follows.

    python test_hdc1080.py --address E8:C6:21:E2:29:78
"""
import statistics
import sys

from minitest import spec
from minitest.runner import standalone

FMT, NAMES, SIZE = spec.FRAMES["hdc1080"]

# What the Zephyr driver returns when it never got a reading: t_sample and
# rh_sample stay zero, which the conversion turns into exactly -40 degC / 0 %.
DEAD_SENSOR = (-40.0, 0.0)


def extra(p):
    p.add_argument("--intervals", default="300,500,1000",
                   help="zu pruefende Intervalle in ms, kommagetrennt")
    p.add_argument("--cycles", type=int, default=6,
                   help="wie viele Messwerte je Intervall abgewartet werden")


async def run(dev, rep, args):
    intervals = [int(x) for x in args.intervals.split(",") if x.strip()]

    async with dev.stream(spec.HDC_DATA) as stream:
        await dev.restart_run()

        rep.section("Grundfunktion")
        cfg, effective = spec.interval_config(0x01, 500, spec.HDC_MIN_INTERVAL_MS)
        await dev.write(spec.HDC_CFG, cfg)
        cap = await stream.capture(max(3.0, args.cycles * effective / 1000), settle=1.0)
        frames = cap.frames(FMT, NAMES, SIZE)

        if not rep.check("Daten kommen an", len(frames) > 0,
                         f"{len(cap)} Notifications, {len(frames)} Messwerte"):
            await dev.write(spec.HDC_CFG, bytes([0x00, 0x05]))
            return

        bad = [n for n in cap.lengths if n % SIZE]
        rep.check(f"Paketlaengen sind Vielfache von {SIZE} Byte",
                  not bad, f"Laengen {dict(cap.lengths)}")

        temps = [f["temperature"] for f in frames]
        hums = [f["humidity"] for f in frames]
        dead = (all(t == DEAD_SENSOR[0] for t in temps)
                and all(h == DEAD_SENSOR[1] for h in hums))
        rep.metric("hdc.alive", 0.0 if dead else 1.0,
                   label="HDC1080 antwortet (1=ja)")
        if dead:
            rep.check("Sensor antwortet", False,
                      "durchgehend -40.0 degC / 0.0 %RH - der Treiber hat nie einen "
                      "Messwert bekommen. Beim Booten meldet er dann "
                      "'TI_HDC: Failed to get correct manufacturer ID'. Auf I2C-Adresse "
                      "0x40 antwortet nichts.")
        else:
            rep.check("Sensor antwortet", True,
                      f"T {min(temps):.2f}..{max(temps):.2f} degC, "
                      f"RH {min(hums):.2f}..{max(hums):.2f} %")
            for field, values in (("temperature", temps), ("humidity", hums)):
                lo, hi = spec.PLAUSIBLE[field]
                mean = statistics.mean(values)
                unit = "degC" if field == "temperature" else "%RH"
                rep.metric(f"hdc.{field}_mean", mean, unit=unit,
                           label=f"HDC {field} Mittelwert")
                rep.metric(f"hdc.{field}_noise", statistics.pstdev(values),
                           unit=unit, label=f"HDC {field} Rauschen (stdev)")
                rep.within(f"{field} plausibel", mean, lo, hi)

        st = cap.timestamp_stats(frames)
        rep.check("Zeitstempel streng aufsteigend", st and st["non_monotonic"] == 0,
                  f"{st['non_monotonic']} Rueckspruenge" if st else "")

        rep.section("Konfiguration: Intervall")
        for want_ms in intervals:
            cfg, effective = spec.interval_config(0x01, want_ms, spec.HDC_MIN_INTERVAL_MS)
            await dev.write(spec.HDC_CFG, cfg)
            window = max(3.0, args.cycles * effective / 1000)
            cap = await stream.capture(window, settle=effective / 1000 + 0.5)
            frames = cap.frames(FMT, NAMES, SIZE)
            st = cap.timestamp_stats(frames)
            note = "" if want_ms == effective else f" (auf {effective} ms angehoben)"
            if not st or st["median_dt_s"] is None:
                rep.check(f"Intervall {want_ms} ms{note}", False,
                          f"nur {len(frames)} Messwerte in {window:.1f}s")
                continue
            rep.near(f"Intervall {want_ms} ms{note}",
                     st["median_dt_s"] * 1000, effective,
                     tol_rel=0.20, tol_abs=60, unit=" ms")

        rep.section("Abschalten")
        await dev.write(spec.HDC_CFG, bytes([0x00, 0x05]))
        cap = await stream.capture(2.0, settle=1.0)
        rep.check("nach enable=0 kommen keine Daten mehr", len(cap) == 0,
                  f"{len(cap)} Notifications trotzdem erhalten")


if __name__ == "__main__":
    sys.exit(standalone(run, "hdc1080", extra))

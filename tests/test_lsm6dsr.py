#!/usr/bin/env python3
"""LSM6DSR (accelerometer, gyroscope) of a phyphox:mini.

The device has to lie still on a table for this. Besides the pass/fail
checks the test records a reference measurement of noise and offset, which
is what makes a comparison in a year worthwhile: a gyroscope that has aged
shows a drifting zero rate long before anything else goes wrong.

    python test_lsm6dsr.py --address E8:C6:21:E2:29:78
"""
import asyncio
import math
import statistics
import struct
import sys
import time

from minitest import spec
from minitest.ble import Capture
from minitest.runner import standalone

G = 9.81
FLOAT_FMT, FLOAT_NAMES, FLOAT_SIZE = spec.FRAMES["lsm_float"]

# Fixed configuration for the reference measurement. Do not change it, the
# stored history is only comparable if every run used the same settings.
REF_RATE = 0x04          # 104 Hz
REF_ACC_RANGE = 0x00     # 2 g, the most sensitive range
REF_GYR_RANGE = 0x02     # 125 deg/s, the most sensitive range
REF_EVENT_SIZE = 40
REF_SECONDS = 8.0

# The highest rates need a short connection interval, otherwise the link
# cannot carry the data (6667 Hz arrives as ~3800 Hz at 30 ms). Only the
# rate sweep uses it: at 15 ms every HDC1080 reading of the fob fails, so
# the rest of the suite stays on the longer default.
RATES_CONN_PARAMS = spec.conn_params(15.0, 15.0, 0, 2500)


def extra(p):
    p.add_argument("--window", type=float, default=3.0,
                   help="Messfenster je Schritt in Sekunden")
    p.add_argument("--reference-seconds", type=float, default=REF_SECONDS,
                   help="Dauer der Referenzmessung fuer Rauschen und Offset")
    p.add_argument("--skip-rates", action="store_true",
                   help="den Durchlauf ueber alle Datenraten auslassen")


def decode_int16(cap, scale=1.0):
    """int16 packets: one float32 timestamp, then event_size triples."""
    out = []
    for _, p in cap.packets:
        if len(p) < 10:
            continue
        t0 = struct.unpack_from("<f", p, 0)[0]
        for i in range((len(p) - 4) // 6):
            x, y, z = struct.unpack_from("<hhh", p, 4 + 6 * i)
            out.append({"t0": t0, "x": x * scale, "y": y * scale, "z": z * scale})
    return out


def int16_rate(cap, event_size):
    """Sample rate from the packet timestamps, which come from the nRF clock."""
    ts = [struct.unpack_from("<f", p, 0)[0] for _, p in cap.packets if len(p) >= 4]
    if len(ts) < 3 or ts[-1] <= ts[0]:
        return None
    return event_size * (len(ts) - 1) / (ts[-1] - ts[0])


async def capture_both(a, b, seconds, settle=0.0):
    """One window, both characteristics, so the two streams line up."""
    if settle:
        await asyncio.sleep(settle)
    a.packets.clear()
    b.packets.clear()
    t0 = time.monotonic()
    await asyncio.sleep(seconds)
    dur = time.monotonic() - t0
    return Capture(list(a.packets), dur), Capture(list(b.packets), dur)


async def run(dev, rep, args):
    async with dev.stream(spec.ACC_DATA) as acc_s, dev.stream(spec.GYR_DATA) as gyr_s:
        await dev.restart_run()
        both = spec.LSM_ACC_BIT | spec.LSM_GYR_BIT

        # ================================================================
        rep.section("Grundfunktion (float, 104 Hz, 16 g)")
        await dev.write(spec.LSM_CFG, spec.lsm_config(
            both, REF_RATE, range_acc=0x01, range_gyr=REF_GYR_RANGE,
            fmt=spec.LSM_FORMAT_FLOAT, event_size=15))
        acc, gyr = await capture_both(acc_s, gyr_s, args.window, settle=1.0)
        acc_f = acc.frames(FLOAT_FMT, FLOAT_NAMES, FLOAT_SIZE)
        gyr_f = gyr.frames(FLOAT_FMT, FLOAT_NAMES, FLOAT_SIZE)

        if not rep.check("Beschleunigungsdaten kommen an", len(acc_f) > 0,
                         f"{len(acc)} Pakete, {len(acc_f)} Messwerte"):
            await dev.write(spec.LSM_CFG, spec.lsm_config(0x00, REF_RATE))
            return
        rep.check("Gyroskopdaten kommen an", len(gyr_f) > 0,
                  f"{len(gyr)} Pakete, {len(gyr_f)} Messwerte")

        st = acc.timestamp_stats(acc_f)
        rep.check("Zeitstempel streng aufsteigend", st and st["non_monotonic"] == 0,
                  f"{st['non_monotonic']} Rueckspruenge" if st else "")

        mag = statistics.mean(math.dist((0, 0, 0), (f["x"], f["y"], f["z"]))
                              for f in acc_f)
        rep.near("Betrag der Beschleunigung in Ruhe", mag, G,
                 tol_rel=0.05, unit=" m/s^2")

        # ================================================================
        rep.section("Paketlaenge folgt event_size (float)")
        for event_size in (2, 15):
            await dev.write(spec.LSM_CFG, spec.lsm_config(
                spec.LSM_ACC_BIT, REF_RATE, range_acc=0x01,
                fmt=spec.LSM_FORMAT_FLOAT, event_size=event_size))
            cap, _ = await capture_both(acc_s, gyr_s, args.window, settle=1.0)
            if not len(cap):
                rep.check(f"event_size={event_size}", False, "keine Pakete")
                continue
            common = cap.lengths.most_common(1)[0][0]
            expected = event_size * FLOAT_SIZE
            ok = common == expected
            detail = f"{common} Byte, erwartet {expected}"
            if not ok and common == spec.LSM_FLOAT_MAX_EVENT_SIZE * FLOAT_SIZE:
                detail += (". Die Firmware sendet immer 240 Byte - sie ist aelter als "
                           "der Paketlaengen-Fix. Nur event_size=15 liefert dort "
                           "brauchbare Daten, alles andere haengt Muell an.")
            rep.check(f"float event_size={event_size} -> {expected} Byte", ok, detail)
            if event_size == 2:
                rep.metric("lsm.float_packet_len_follows_event_size", 1.0 if ok else 0.0,
                           label="Float-Paketlaenge folgt event_size (1=ja)")

        rep.section("Paketlaenge folgt event_size (int16)")
        for event_size in (10, 40):
            await dev.write(spec.LSM_CFG, spec.lsm_config(
                spec.LSM_ACC_BIT, REF_RATE, range_acc=0x01,
                fmt=spec.LSM_FORMAT_INT16, event_size=event_size))
            cap, _ = await capture_both(acc_s, gyr_s, args.window, settle=1.0)
            if not len(cap):
                rep.check(f"int16 event_size={event_size}", False, "keine Pakete")
                continue
            common = cap.lengths.most_common(1)[0][0]
            expected = 4 + 6 * event_size
            rep.check(f"int16 event_size={event_size} -> {expected} Byte",
                      common == expected, f"{common} Byte, erwartet {expected}")

        # ================================================================
        rep.section("Messbereiche (int16, Betrag muss immer 9,81 ergeben)")
        for rng, full_scale in sorted(spec.LSM_ACC_RANGES.items(),
                                      key=lambda kv: kv[1]):
            scale = spec.LSM_ACC_MG_PER_LSB[rng] * G / 1000.0      # LSB -> m/s^2
            await dev.write(spec.LSM_CFG, spec.lsm_config(
                spec.LSM_ACC_BIT, REF_RATE, range_acc=rng,
                fmt=spec.LSM_FORMAT_INT16, event_size=REF_EVENT_SIZE))
            cap, _ = await capture_both(acc_s, gyr_s, args.window, settle=1.0)
            samples = decode_int16(cap, scale)
            if len(samples) < 10:
                rep.check(f"Bereich +/-{full_scale} g", False,
                          f"nur {len(samples)} Messwerte")
                continue
            m = statistics.mean(math.dist((0, 0, 0), (s["x"], s["y"], s["z"]))
                                for s in samples)
            rep.near(f"Bereich +/-{full_scale} g", m, G, tol_rel=0.08, unit=" m/s^2")

        rep.section("Messbereiche (float)")
        for rng, full_scale in sorted(spec.LSM_ACC_RANGES.items(),
                                      key=lambda kv: kv[1]):
            await dev.write(spec.LSM_CFG, spec.lsm_config(
                spec.LSM_ACC_BIT, REF_RATE, range_acc=rng,
                fmt=spec.LSM_FORMAT_FLOAT, event_size=15))
            cap, _ = await capture_both(acc_s, gyr_s, args.window, settle=1.0)
            frames = cap.frames(FLOAT_FMT, FLOAT_NAMES, FLOAT_SIZE)
            if len(frames) < 10:
                rep.check(f"float +/-{full_scale} g", False,
                          f"nur {len(frames)} Messwerte")
                continue
            m = statistics.mean(math.dist((0, 0, 0), (f["x"], f["y"], f["z"]))
                                for f in frames)
            detail = f"{m:.2f} m/s^2, erwartet {G:.2f}"
            if abs(m - G * G) < 0.15 * G * G:
                # Firmware before the scaling fix converted to m/s^2 in
                # get_acc_si() and multiplied by 9.81 once more when building
                # the float packet. Only 16 g came out right.
                detail += (" - um 9,81 zu gross: Firmware ohne den "
                           "Skalierungs-Fix, bitte aktualisieren")
            rep.check(f"float +/-{full_scale} g liefert m/s^2",
                      abs(m - G) < 0.08 * G, detail)

        # ================================================================
        rep.section("Datenraten")
        if args.skip_rates:
            rep.skip("Datenraten-Durchlauf", "per --skip-rates abgewaehlt")
        else:
            ratios = []
            await dev.set_conn_params(RATES_CONN_PARAMS)
            rep.info("Verbindungsintervall", "15 ms fuer den Datenraten-Durchlauf")
            for rate_byte, nominal in sorted(spec.LSM_RATES.items()):
                if nominal < 100:
                    continue          # unter 100 Hz dauert das Fenster zu lange
                await dev.write(spec.LSM_CFG, spec.lsm_config(
                    spec.LSM_ACC_BIT, rate_byte, range_acc=0x01,
                    fmt=spec.LSM_FORMAT_INT16, event_size=REF_EVENT_SIZE))
                cap, _ = await capture_both(acc_s, gyr_s, args.window, settle=1.0)
                measured = int16_rate(cap, REF_EVENT_SIZE)
                if measured is None:
                    rep.check(f"{nominal:g} Hz", False, "zu wenige Pakete")
                    continue
                ratio = measured / nominal
                ratios.append(ratio)
                rep.check(f"{nominal:g} Hz gemessen", 0.8 <= ratio <= 1.25,
                          f"{measured:.1f} Hz, Faktor {ratio:.3f}")
            # back to the session setting, or to 30 ms if there was none
            await dev.set_conn_params(dev.conn_params or spec.conn_params())
            if ratios:
                rep.metric("lsm.odr_ratio", statistics.mean(ratios),
                           label="gemessene ODR / Nennrate")
                rep.info("Hinweis zur ODR",
                         "Der Sensortakt weicht systematisch von der Nennrate ab. "
                         "Wer Frequenzen bestimmt, sollte die Rate aus den "
                         "Zeitstempeln rechnen.")

        # ================================================================
        rep.section(f"Referenzmessung: Rauschen und Offset in Ruhe "
                    f"({args.reference_seconds:.0f}s)")
        rep.info("Konfiguration", f"int16, {spec.LSM_RATES[REF_RATE]:g} Hz, "
                                  f"Acc +/-{spec.LSM_ACC_RANGES[REF_ACC_RANGE]} g, "
                                  f"Gyro +/-{spec.LSM_GYR_RANGES[REF_GYR_RANGE]} deg/s")
        await dev.write(spec.LSM_CFG, spec.lsm_config(
            both, REF_RATE, range_acc=REF_ACC_RANGE, range_gyr=REF_GYR_RANGE,
            fmt=spec.LSM_FORMAT_INT16, event_size=REF_EVENT_SIZE))
        acc, gyr = await capture_both(acc_s, gyr_s, args.reference_seconds, settle=2.0)

        acc_mg = decode_int16(acc, spec.LSM_ACC_MG_PER_LSB[REF_ACC_RANGE])
        gyr_dps = decode_int16(gyr, spec.LSM_GYR_MDPS_PER_LSB[REF_GYR_RANGE] / 1000.0)
        # int16 packets carry one timestamp each, so these plot against the index
        rep.raw("lsm.reference_acc",
                {f"Acc {a.upper()}": ([s[a] for s in acc_mg], "mg") for a in "xyz"},
                label=f"Beschleunigung in Ruhe ({spec.LSM_RATES[REF_RATE]:g} Hz)")
        rep.raw("lsm.reference_gyr",
                {f"Gyro {a.upper()}": ([s[a] for s in gyr_dps], "deg/s") for a in "xyz"},
                label=f"Drehrate in Ruhe ({spec.LSM_RATES[REF_RATE]:g} Hz)")

        if len(acc_mg) < 50:
            rep.check("Referenzmessung Beschleunigung", False,
                      f"nur {len(acc_mg)} Messwerte")
        else:
            for axis in ("x", "y", "z"):
                values = [s[axis] for s in acc_mg]
                rep.metric(f"lsm.acc_noise_{axis}_mg", statistics.pstdev(values),
                           unit="mg", label=f"Acc-Rauschen {axis.upper()} (stdev)")
            mag = statistics.mean(math.dist((0, 0, 0), (s["x"], s["y"], s["z"]))
                                  for s in acc_mg) / 1000.0        # mg -> g
            rep.metric("lsm.acc_magnitude_g", mag, unit="g",
                       label="Betrag der Beschleunigung in Ruhe")
            rep.metric("lsm.acc_offset_mg", (mag - 1.0) * 1000.0, unit="mg",
                       label="Abweichung des Betrags von 1 g")
            rep.near("Betrag in Ruhe ist 1 g", mag, 1.0, tol_rel=0.05, unit=" g")

        if len(gyr_dps) < 50:
            rep.check("Referenzmessung Gyroskop", False,
                      f"nur {len(gyr_dps)} Messwerte")
        else:
            for axis in ("x", "y", "z"):
                values = [s[axis] for s in gyr_dps]
                bias = statistics.mean(values)
                rep.metric(f"lsm.gyr_bias_{axis}_dps", bias, unit="deg/s",
                           label=f"Gyro-Nullpunkt {axis.upper()}")
                rep.metric(f"lsm.gyr_noise_{axis}_dps", statistics.pstdev(values),
                           unit="deg/s", label=f"Gyro-Rauschen {axis.upper()} (stdev)")
                rep.within(f"Gyro-Nullpunkt {axis.upper()} im Rahmen",
                           abs(bias), 0.0, 5.0, unit=" deg/s")

        # ================================================================
        rep.section("Enable-Bits")
        await dev.write(spec.LSM_CFG, spec.lsm_config(
            spec.LSM_ACC_BIT, REF_RATE, fmt=spec.LSM_FORMAT_INT16,
            event_size=REF_EVENT_SIZE))
        acc, gyr = await capture_both(acc_s, gyr_s, 2.0, settle=1.0)
        rep.check("nur Beschleunigung: Gyroskop schweigt",
                  len(acc) > 0 and len(gyr) == 0,
                  f"Acc {len(acc)} Pakete, Gyro {len(gyr)} Pakete")

        await dev.write(spec.LSM_CFG, spec.lsm_config(
            spec.LSM_GYR_BIT, REF_RATE, fmt=spec.LSM_FORMAT_INT16,
            event_size=REF_EVENT_SIZE))
        acc, gyr = await capture_both(acc_s, gyr_s, 2.0, settle=1.0)
        rep.check("nur Gyroskop: Beschleunigung schweigt",
                  len(gyr) > 0 and len(acc) == 0,
                  f"Acc {len(acc)} Pakete, Gyro {len(gyr)} Pakete")
        rate = int16_rate(gyr, REF_EVENT_SIZE)
        rep.check("nur Gyroskop: Rate stimmt",
                  rate is not None and 0.8 <= rate / spec.LSM_RATES[REF_RATE] <= 1.25,
                  f"{rate:.1f} Hz" if rate else "zu wenige Pakete")

        # Both sensors share one data-ready interrupt. Were both DRDY signals
        # routed to INT1, some samples would be read twice: more packets than
        # the rate allows and repeated timestamps.
        await dev.write(spec.LSM_CFG, spec.lsm_config(
            spec.LSM_ACC_BIT | spec.LSM_GYR_BIT, REF_RATE, fmt=spec.LSM_FORMAT_INT16,
            event_size=REF_EVENT_SIZE))
        acc, gyr = await capture_both(acc_s, gyr_s, 4.0, settle=1.0)
        rep.check("beide: gleich viele Pakete",
                  len(acc) > 0 and abs(len(acc) - len(gyr)) <= 1,
                  f"Acc {len(acc)} Pakete, Gyro {len(gyr)} Pakete")
        for label, cap in (("Beschleunigung", acc), ("Gyroskop", gyr)):
            rate = int16_rate(cap, REF_EVENT_SIZE)
            rep.check(f"beide: Rate {label} nicht doppelt",
                      rate is not None and 0.8 <= rate / spec.LSM_RATES[REF_RATE] <= 1.25,
                      f"{rate:.1f} Hz, Nennrate {spec.LSM_RATES[REF_RATE]:g} Hz"
                      if rate else "zu wenige Pakete")

        rep.section("Abschalten")
        await dev.write(spec.LSM_CFG, spec.lsm_config(0x00, REF_RATE))
        acc, gyr = await capture_both(acc_s, gyr_s, 2.0, settle=1.0)
        rep.check("nach enable=0 kommen keine Daten mehr",
                  len(acc) == 0 and len(gyr) == 0,
                  f"Acc {len(acc)}, Gyro {len(gyr)} Pakete")


if __name__ == "__main__":
    sys.exit(standalone(run, "lsm6dsr", extra))

#!/usr/bin/env python3
"""Standalone logging (datalog) of a phyphox:mini.

Logging only runs while no client is connected, so the test configures the
device, drops the link, waits, reconnects and then asks for the data. It
checks that as many records come back as were written, that the record
layout follows the sensor mask, and that the age filter works.

The original datalog configuration is restored at the end.

    python test_datalog.py --address E8:C6:21:E2:29:78 --log-seconds 180
"""
import asyncio
import struct
import sys
import time

from minitest import spec
from minitest.runner import standalone


def extra(p):
    p.add_argument("--interval", type=int, default=2,
                   help="Aufzeichnungsintervall in Sekunden")
    p.add_argument("--log-seconds", type=float, default=180.0,
                   help="wie lange ohne Verbindung aufgezeichnet wird")
    p.add_argument("--max-age", type=int, default=2,
                   help="Minuten fuer den zweiten, gefilterten Dump (0 = aus)")
    p.add_argument("--idle", type=float, default=4.0,
                   help="Ruhezeit, nach der ein Dump als beendet gilt")
    p.add_argument("--no-restore", dest="restore", action="store_false",
                   help="die urspruengliche Datalog-Konfiguration nicht zuruecksetzen")


def parse_state(raw):
    if len(raw) < 5:
        return None
    return {"running": raw[0], "mask": raw[1],
            "interval_s": struct.unpack_from("<H", raw, 2)[0], "bthome": raw[4]}


def parse_records(packets, mask):
    size = spec.datalog_record_size(mask)
    names = [n for bit, n in spec.DL_FIELDS if mask & bit]
    fmt = "<i" + "f" * len(names)
    records, bad = [], []
    for _, p in packets:
        if len(p) % size:
            bad.append(len(p))
        for off in range(0, len(p) - size + 1, size):
            values = struct.unpack_from(fmt, p, off)
            records.append(dict(zip(["t"] + names, values)))
    return records, size, bad


async def collect_dump(dev, stream, mask, cmd_param, idle, timeout=180.0):
    """Ask for a dump and wait until the notifications stop."""
    stream.packets.clear()
    t0 = time.monotonic()
    await dev.write(spec.DATALOG_CFG,
                    spec.datalog_command(spec.DL_DUMP, mask, cmd_param))
    write_returned = time.monotonic() - t0
    seen, quiet_since = -1, time.monotonic()
    while time.monotonic() - t0 < timeout:
        if len(stream.packets) != seen:
            seen, quiet_since = len(stream.packets), time.monotonic()
        if time.monotonic() - quiet_since > idle:
            break
        await asyncio.sleep(0.2)
    return list(stream.packets), write_returned


async def run(dev, rep, args):
    rep.section("Konfiguration lesen und schreiben")
    original = parse_state(await dev.read(spec.DATALOG_CFG))
    if not rep.check("Konfiguration lesbar", original is not None, str(original)):
        return
    rep.info("Ausgangszustand", str(original))

    probe_mask = spec.DL_TEMP | spec.DL_PRESSURE
    await dev.write(spec.DATALOG_CFG,
                    spec.datalog_command(spec.DL_START, probe_mask, 77))
    await asyncio.sleep(0.4)
    got = parse_state(await dev.read(spec.DATALOG_CFG))
    rep.check("geschriebene Konfiguration wird zurueckgelesen",
              got and got["mask"] == probe_mask and got["interval_s"] == 77
              and got["running"] == 1,
              f"gelesen {got}")
    rep.check("Teilmaske ergibt kleineren Datensatz",
              spec.datalog_record_size(probe_mask) == 12,
              f"{spec.datalog_record_size(probe_mask)} Byte bei 2 Groessen")

    # ------------------------------------------------------------------
    rep.section("Aufzeichnen")
    mask = spec.DL_ALL
    await dev.write(spec.DATALOG_CFG, spec.datalog_command(spec.DL_ERASE))
    await asyncio.sleep(1.5)
    await dev.write(spec.DATALOG_CFG,
                    spec.datalog_command(spec.DL_START, mask, args.interval))
    await asyncio.sleep(0.4)
    got = parse_state(await dev.read(spec.DATALOG_CFG))
    rep.check("Aufzeichnung laeuft", got and got["running"] == 1, str(got))

    await dev.disconnect()
    t_disconnect = time.time()
    rep.info("getrennt", f"zeichnet {args.log_seconds:.0f}s ohne Verbindung auf")
    await asyncio.sleep(args.log_seconds)
    await dev.reconnect()
    logged_for = time.time() - t_disconnect
    expected = logged_for / args.interval
    rep.info("wieder verbunden",
             f"{logged_for:.1f}s getrennt, erwartet etwa {expected:.0f} Datensaetze")

    # ------------------------------------------------------------------
    rep.section("Alle Daten abrufen")
    async with dev.stream(spec.DATALOG_DATA) as stream:
        await dev.restart_run()
        packets, write_returned = await collect_dump(dev, stream, mask, 0, args.idle)
        records, size, bad = parse_records(packets, mask)

        rep.check("Datensaetze kommen an", len(records) > 0,
                  f"{len(packets)} Pakete, {len(records)} Datensaetze")
        rep.check(f"Paketlaengen sind Vielfache von {size} Byte", not bad,
                  f"abweichend: {bad}")
        # Aufzeichnen beginnt erst, wenn die Verbindung wirklich getrennt ist,
        # und endet schon beim Verbindungsaufbau - Scan und Connect kosten
        # einige Sekunden Aufzeichnungszeit. Deshalb eine einseitige Grenze.
        overhead_s = 12.0
        lower = max(0.0, (logged_for - overhead_s) / args.interval)
        rep.check("Anzahl passt zur Aufzeichnungsdauer",
                  lower <= len(records) <= expected + 1,
                  f"{len(records)} Datensaetze, erwartet {lower:.0f}..{expected:.0f} "
                  f"(bis zu {overhead_s:.0f}s gehen fuer Trennen und "
                  f"Wiederverbinden ab)")
        rep.metric("datalog.records_expected", expected,
                   label="erwartete Datensaetze")
        rep.metric("datalog.records_received", len(records),
                   label="empfangene Datensaetze")
        if expected:
            rep.metric("datalog.completeness", len(records) / expected,
                       label="empfangen / erwartet")

        if records:
            ts = [r["t"] for r in records]
            deltas = [b - a for a, b in zip(ts, ts[1:])]
            rep.check("Zeitstempel streng aufsteigend",
                      all(d > 0 for d in deltas),
                      f"{sum(1 for d in deltas if d <= 0)} Rueckspruenge")
            off = [d for d in deltas if d != args.interval]
            rep.check(f"Abstand betraegt {args.interval}s",
                      len(off) <= max(1, len(deltas) // 20),
                      f"{len(off)} von {len(deltas)} abweichend")
            for bit, name in spec.DL_FIELDS:
                if not mask & bit:
                    continue
                values = [r[name] for r in records]
                lo, hi = spec.PLAUSIBLE[name]
                inside = sum(1 for v in values if lo <= v <= hi)
                rep.check(f"{name} plausibel",
                          inside >= 0.9 * len(values),
                          f"{inside} von {len(values)} in {lo:g}..{hi:g}, "
                          f"Spanne {min(values):.2f}..{max(values):.2f}")

        rate = len(records) / write_returned if write_returned else 0
        rep.metric("datalog.dump_records_per_s", rate,
                   label="Abrufgeschwindigkeit")
        rep.info("Abruf blockiert den Schreibvorgang",
                 f"{write_returned:.2f}s fuer {len(packets)} Pakete. Die Firmware "
                 f"sendet alles innerhalb des GATT-Writes, mit 100 ms Pause je Paket.")

        # --------------------------------------------------------------
        if args.max_age:
            rep.section(f"Nur die letzten {args.max_age} Minuten abrufen")
            packets, _ = await collect_dump(dev, stream, mask, args.max_age, args.idle)
            subset, _, _ = parse_records(packets, mask)
            want = min(len(records), args.max_age * 60 / args.interval)
            rep.check("Altersfilter liefert weniger Datensaetze",
                      0 < len(subset) <= len(records),
                      f"{len(subset)} von {len(records)}")
            rep.near("Anzahl passt zum Altersfilter", len(subset), want,
                     tol_rel=0.25, tol_abs=5)

        await dev.clear_run()

    # ------------------------------------------------------------------
    rep.section("Aufraeumen")
    if not args.restore:
        rep.skip("urspruengliche Konfiguration", "per --no-restore abgewaehlt")
        return
    if original["mask"] and original["interval_s"]:
        await dev.write(spec.DATALOG_CFG, spec.datalog_command(
            spec.DL_START, original["mask"], original["interval_s"]))
        if not original["running"]:
            await asyncio.sleep(0.3)
            await dev.write(spec.DATALOG_CFG, spec.datalog_command(spec.DL_STOP))
    else:
        await dev.write(spec.DATALOG_CFG, spec.datalog_command(spec.DL_STOP))
    await asyncio.sleep(0.4)
    restored = parse_state(await dev.read(spec.DATALOG_CFG))
    rep.check("urspruengliche Konfiguration wiederhergestellt",
              restored and restored["mask"] == original["mask"]
              and restored["interval_s"] == original["interval_s"]
              and restored["running"] == original["running"],
              f"{restored} vs urspruenglich {original}")


if __name__ == "__main__":
    sys.exit(standalone(run, "datalog", extra))

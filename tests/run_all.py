#!/usr/bin/env python3
"""Run every phyphox:mini test against one board, in one connection.

    python run_all.py --address E8:C6:21:E2:29:78
    python run_all.py --board A19 --fast            # kurze Fassung
    python run_all.py --board A19 --only lsm6dsr,datalog
    python run_all.py --all --fast                  # alle Boards in Reichweite

All results land in the shared measurement store, tagged with the same run
id, so make_report.py can show them together.

--all scans for every phyphox:mini in range and tests them one after the
other, never in parallel: the datalog test drops the connection on purpose
and the LSM reference measurement wants a board lying still. Devices that
advertise the name but lack the phyphox:mini characteristics (other
firmware, e.g. ETS2) are skipped. At the end the HTML report is rebuilt.
"""
import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import make_report                                                 # noqa: E402
import test_bmp581, test_datalog, test_device_info, test_hdc1080  # noqa: E402
import test_lsm6dsr, test_stcc4                                    # noqa: E402
from bleak import BleakScanner                                     # noqa: E402
from minitest import spec, store                                   # noqa: E402
from minitest.ble import MiniDevice, add_common_arguments          # noqa: E402
from minitest.ble import conn_params_from_args                      # noqa: E402
from minitest.report import Report                                 # noqa: E402
from minitest.runner import identify, run_with_device              # noqa: E402

# name, run function, per test defaults, short-run overrides
TESTS = [
    ("device-info", test_device_info.run, {}, {}),
    ("bmp581", test_bmp581.run,
     {"window": 4.0, "skip_oversampling": False},
     {"window": 2.0, "skip_oversampling": True}),
    ("hdc1080", test_hdc1080.run,
     {"intervals": "300,500,1000", "cycles": 6},
     {"intervals": "500", "cycles": 4}),
    ("stcc4", test_stcc4.run,
     {"intervals": "1000,2000", "cycles": 8, "warmup": 6.0},
     {"intervals": "1000", "cycles": 4, "warmup": 4.0}),
    ("lsm6dsr", test_lsm6dsr.run,
     {"window": 3.0, "reference_seconds": 8.0, "skip_rates": False},
     {"window": 1.5, "reference_seconds": 5.0, "skip_rates": True}),
    ("datalog", test_datalog.run,
     {"interval": 2, "log_seconds": 180.0, "max_age": 2, "idle": 4.0,
      "restore": True},
     {"interval": 1, "log_seconds": 45.0, "max_age": 0, "idle": 3.0,
      "restore": True}),
]

# a device only counts as a phyphox:mini if it has these
REQUIRED_CHARS = (spec.RUN_CONTROL, spec.LSM_CFG)


def build_args():
    p = argparse.ArgumentParser(prog="run_all")
    add_common_arguments(p)
    store.add_store_arguments(p)
    p.add_argument("--run-id", help="eigene Kennung statt einer erzeugten")
    p.add_argument("--only", help="nur diese Tests, kommagetrennt")
    p.add_argument("--skip", help="diese Tests auslassen, kommagetrennt")
    p.add_argument("--fast", action="store_true",
                   help="kurze Fassung: kleinere Fenster, keine langen Durchlaeufe")
    p.add_argument("--log-seconds", type=float,
                   help="Aufzeichnungsdauer des Datalog-Tests uebersteuern")
    p.add_argument("--all", action="store_true",
                   help="alle phyphox:mini in Reichweite nacheinander testen")
    p.add_argument("--scan-seconds", type=float, default=60.0,
                   help="Scandauer fuer --all (Standard 60 s; die Boards senden nur "
                        "alle ~2 s und Windows scannt nicht durchgehend)")
    args = p.parse_args()
    if args.all and (args.address or args.board):
        p.error("--all laesst sich nicht mit --address oder --board kombinieren")
    return args


def selected(args):
    only = {s.strip() for s in args.only.split(",")} if args.only else None
    skip = {s.strip() for s in args.skip.split(",")} if args.skip else set()
    out = []
    for name, fn, defaults, fast in TESTS:
        if only is not None and name not in only:
            continue
        if name in skip:
            continue
        out.append((name, fn, dict(fast if args.fast else defaults)))
    return out


async def scan_boards(name_prefix, seconds):
    """[(address, advertised name)] of every device whose name matches."""
    found = await BleakScanner.discover(timeout=seconds, return_adv=True)
    boards = [(d.address, (a.local_name or d.name or "").strip())
              for d, a in found.values()
              if (a.local_name or d.name or "").startswith(name_prefix)]
    return sorted(boards, key=lambda b: b[1])


async def test_board(args, tests, log, address=None):
    """Run the selected tests on one board.

    Returns (label, reports, problem). problem is None when the tests ran,
    otherwise why they did not: no connection, or not a phyphox:mini.
    """
    label = address or args.board or args.address or "?"
    reports = []
    try:
        async with MiniDevice(address=address or args.address, name_prefix=args.name_prefix,
                              scan_timeout=args.scan_timeout, log=log,
                              board=None if address else args.board,
                              conn_params=conn_params_from_args(args)) as dev:
            ident = await identify(dev)
            label = f"{ident[1] or '?'} ({ident[0]})"
            missing = [c for c in REQUIRED_CHARS if not dev.has_char(c)]
            if missing:
                return label, reports, ("uebersprungen: keine phyphox:mini-Firmware "
                                        f"(fehlt {', '.join(c[4:8] for c in missing)})")
            print(f"Board {label}, Firmware {ident[2]}", file=sys.stderr)
            for name, fn, defaults in tests:
                print(f"\n{'=' * 70}\n== {name}\n{'=' * 70}", file=sys.stderr)
                sub = argparse.Namespace(**vars(args))
                for k, v in defaults.items():
                    setattr(sub, k, v)
                if name == "datalog" and args.log_seconds is not None:
                    sub.log_seconds = args.log_seconds
                rep = Report(name, quiet=args.quiet)
                await run_with_device(fn, dev, rep, sub, identity=ident)
                rep.summary()
                reports.append(rep)
            try:
                await dev.all_sensors_off()
            except Exception:                     # noqa: BLE001
                pass
    except Exception as exc:                      # noqa: BLE001
        return label, reports, f"Verbindung fehlgeschlagen: {type(exc).__name__}: {exc}"
    return label, reports, None


def print_reports(reports):
    """One line per test; returns 1 if any test failed."""
    worst = 0
    for rep in reports:
        c = rep.counts()
        mark = "FAIL" if c["FAIL"] else ("warn" if c["WARN"] else " ok ")
        print(f"  [{mark}] {rep.name:<13} {c['PASS']:>3} ok  {c['FAIL']:>3} fehl  "
              f"{c['WARN']:>3} warn  {len(rep.metrics):>3} Messwerte", file=sys.stderr)
        worst = max(worst, 1 if c["FAIL"] else 0)
    return worst


async def run_single(args, tests, log):
    _, reports, problem = await test_board(args, tests, log)
    print(f"\n{'=' * 70}\nZusammenfassung  (Durchlauf {args.run_id})\n{'=' * 70}",
          file=sys.stderr)
    if problem:
        print(f"  {problem}", file=sys.stderr)
        return 1
    worst = print_reports(reports)
    total = sum(len(r.metrics) for r in reports)
    print(f"\n  {total} Messwerte in {args.store}", file=sys.stderr)
    print(f"  HTML erzeugen:  python make_report.py", file=sys.stderr)
    return worst


async def run_every_board(args, tests, log):
    print(f"Suche {args.scan_seconds:.0f} s nach '{args.name_prefix}' ...", file=sys.stderr)
    boards = await scan_boards(args.name_prefix, args.scan_seconds)
    if not boards:
        print("kein Board gefunden", file=sys.stderr)
        return 1
    print(f"{len(boards)} gefunden: " + ", ".join(n for _, n in boards), file=sys.stderr)

    results = []
    for i, (address, name) in enumerate(boards, 1):
        print(f"\n{'#' * 70}\n## Board {i}/{len(boards)}: {name} ({address})\n{'#' * 70}",
              file=sys.stderr)
        results.append(await test_board(args, tests, log, address=address))

    print(f"\n{'=' * 70}\nGesamtuebersicht  (Durchlauf {args.run_id})\n{'=' * 70}",
          file=sys.stderr)
    worst = 0
    for label, reports, problem in results:
        if problem:
            mark = "skip" if problem.startswith("uebersprungen") else "FAIL"
            print(f"  [{mark}] {label}: {problem}", file=sys.stderr)
            worst = max(worst, 0 if mark == "skip" else 1)
            continue
        failed = [r.name for r in reports if r.counts()["FAIL"]]
        warned = [r.name for r in reports if r.counts()["WARN"] and r.name not in failed]
        mark = "FAIL" if failed else ("warn" if warned else " ok ")
        detail = (f"fehlgeschlagen: {', '.join(failed)}" if failed
                  else f"Warnungen: {', '.join(warned)}" if warned
                  else f"{len(reports)} Tests bestanden")
        print(f"  [{mark}] {label}: {detail}", file=sys.stderr)
        worst = max(worst, 1 if failed else 0)

    if not args.no_store:
        print("", file=sys.stderr)
        make_report.write_report(args.store)
    return worst


async def main():
    args = build_args()
    args.run_id = args.run_id or store.new_run_id()
    tests = selected(args)
    if not tests:
        print("kein Test ausgewaehlt", file=sys.stderr)
        return 2

    log = (lambda *a: None) if args.quiet else (lambda *a: print(*a, file=sys.stderr))
    print(f"Durchlauf {args.run_id}, {len(tests)} Tests", file=sys.stderr)
    if args.all:
        return await run_every_board(args, tests, log)
    return await run_single(args, tests, log)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

"""Boilerplate so every test script can also be run on its own."""
import argparse
import asyncio
import sys

from . import spec, store
from .ble import MiniDevice, add_common_arguments, conn_params_from_args
from .report import Report


def parse(name, extra=None, argv=None):
    p = argparse.ArgumentParser(prog=name)
    add_common_arguments(p)
    store.add_store_arguments(p)
    p.add_argument("--run-id", help="gemeinsame Kennung, wenn mehrere Tests "
                                    "zu einem Durchlauf gehoeren")
    if extra:
        extra(p)
    return p.parse_args(argv)


async def identify(dev):
    """Board identity that the measurement store keys on."""
    firmware = None
    try:
        firmware = await dev.read_string(spec.DIS_FIRMWARE)
    except Exception:                             # noqa: BLE001 - optional
        pass
    return dev.address, store.short_name(dev.name), firmware


async def run_with_device(run_fn, dev, rep, args, identity=None):
    """Run one test against an already connected device and store the result."""
    try:
        await run_fn(dev, rep, args)
    except Exception as exc:                      # noqa: BLE001 - report, do not crash
        rep.check("Testlauf ohne Ausnahme", False, f"{type(exc).__name__}: {exc}")

    # Stored even without metrics: the report shows pass/fail per board, and
    # a test like device-info has verdicts but nothing to measure.
    if getattr(args, "no_store", False):
        return rep
    board_id, board_name, firmware = identity or await identify(dev)
    record = store.make_record(rep, run_id=args.run_id or store.new_run_id(),
                               board_id=board_id, board_name=board_name,
                               firmware=firmware)
    path = store.append(record, args.store)
    if not args.quiet:
        print(f"      Ergebnis und {len(rep.metrics)} Messwerte angehaengt an {path}",
              file=sys.stderr)
    return rep


async def run_standalone(run_fn, name, args):
    rep = Report(name, quiet=args.quiet)
    log = (lambda *a: None) if args.quiet else (lambda *a: print(*a, file=sys.stderr))
    try:
        async with MiniDevice(address=args.address, name_prefix=args.name_prefix,
                              scan_timeout=args.scan_timeout, log=log, board=args.board,
                              conn_params=conn_params_from_args(args)) as dev:
            await run_with_device(run_fn, dev, rep, args)
    except Exception as exc:                      # noqa: BLE001
        rep.check("Verbindung zum Geraet", False, f"{type(exc).__name__}: {exc}")
    rep.summary()
    if args.json:
        rep.save(args.json)
    return 1 if rep.failed else 0


def standalone(run_fn, name, extra=None, argv=None):
    args = parse(name, extra, argv)
    return asyncio.run(run_standalone(run_fn, name, args))

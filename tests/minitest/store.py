"""Append only measurement store.

One JSON object per line, never rewritten. That keeps it readable, easy to
merge and cheap to grow: measuring the same board again in a year just adds
lines, and the HTML report can then show how a value drifted over time.

Every record carries the board identity, so results from several boards can
live in the same file.
"""
import datetime
import json
import os
import uuid

# 2: adds "checks", and a record is written for every test, not only
#    for those that produced metrics
SCHEMA = 2
DEFAULT_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "results", "measurements.jsonl")


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).replace(
        microsecond=0).isoformat().replace("+00:00", "Z")


def new_run_id():
    return f"{now_iso()}-{uuid.uuid4().hex[:6]}"


def short_name(advertised):
    """'phyphox:mini A24' -> 'A24'."""
    name = (advertised or "").strip()
    prefix = "phyphox:mini"
    return name[len(prefix):].strip() if name.startswith(prefix) else name


def make_record(report, *, run_id, board_id, board_name=None, firmware=None,
                extra=None):
    return {
        "schema": SCHEMA,
        "run_id": run_id,
        "ts": now_iso(),
        "board_id": board_id,
        "board_name": board_name,
        "firmware": firmware,
        "test": report.name,
        "duration_s": report.to_dict()["duration_s"],
        "counts": report.counts(),
        "metrics": report.metrics,
        # every single verdict with its detail text, so the HTML report can
        # show which check failed and why
        "checks": report.entries,
        **(extra or {}),
    }


def append(record, path=DEFAULT_PATH):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return path


def load(path=DEFAULT_PATH):
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError as exc:
                print(f"{path}:{n}: uebersprungen ({exc})")
    return out


def add_store_arguments(parser):
    parser.add_argument("--store", default=DEFAULT_PATH,
                        help="JSONL-Datei fuer die Messwerte")
    parser.add_argument("--no-store", action="store_true",
                        help="Messwerte nicht in die Sammeldatei schreiben")
    return parser

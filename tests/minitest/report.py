"""Collecting and printing test results.

Two kinds of output:

* checks - a name, a verdict and enough detail to see why it went that way
* metrics - named numbers that are worth keeping. Those go into the
  measurement store so the same board can be measured again next year and
  compared, for instance to see whether a sensor has aged.
"""
import json
import math
import sys
import time

PASS, FAIL, WARN, SKIP, INFO = "PASS", "FAIL", "WARN", "SKIP", "INFO"
# raw series longer than this are thinned to every k-th value before storing,
# so a single run stays a few hundred kB at most
MAX_RAW_POINTS = 2000

_MARK = {PASS: "[ ok ]", FAIL: "[FAIL]", WARN: "[warn]", SKIP: "[skip]", INFO: "      "}


class Report:
    def __init__(self, name, quiet=False):
        self.name = name
        self.quiet = quiet
        self.entries = []
        self.metrics = {}
        self.raw_series = {}
        self.started = time.time()
        self._section = None

    # -- structure ---------------------------------------------------------
    def section(self, title):
        self._section = title
        if not self.quiet:
            print(f"\n--- {title} " + "-" * max(0, 62 - len(title)), file=sys.stderr)

    def _add(self, verdict, title, detail=""):
        self.entries.append({"section": self._section, "verdict": verdict,
                             "title": title, "detail": detail})
        if not self.quiet:
            line = f"{_MARK[verdict]} {title}"
            if detail:
                line += f"  --  {detail}"
            print(line, file=sys.stderr, flush=True)
        return verdict == PASS

    # -- verdicts ----------------------------------------------------------
    def check(self, title, ok, detail=""):
        return self._add(PASS if ok else FAIL, title, detail)

    def warn(self, title, detail=""):
        return self._add(WARN, title, detail)

    def skip(self, title, reason=""):
        return self._add(SKIP, title, reason)

    def info(self, title, detail=""):
        return self._add(INFO, title, detail)

    def near(self, title, actual, expected, tol_rel=0.10, tol_abs=0.0, unit=""):
        """Check that a measured value is close to an expected one."""
        if actual is None:
            return self.check(title, False, "kein Messwert")
        tol = max(abs(expected) * tol_rel, tol_abs)
        ok = abs(actual - expected) <= tol
        dev = (actual - expected) / expected * 100 if expected else float("nan")
        return self.check(
            title, ok,
            f"{actual:.4g}{unit} vs erwartet {expected:.4g}{unit} "
            f"({dev:+.1f}%, erlaubt +/-{tol:.4g}{unit})")

    def within(self, title, value, lo, hi, unit=""):
        if value is None:
            return self.check(title, False, "kein Messwert")
        return self.check(title, lo <= value <= hi,
                          f"{value:.4g}{unit} (erlaubt {lo:g}..{hi:g}{unit})")

    # -- metrics -----------------------------------------------------------
    def metric(self, key, value, unit="", label=None, detail=""):
        """Record a number worth keeping across runs.

        `key` must stay stable over the years, it is what the history is
        keyed on. `label` is the human readable name for the HTML report.
        """
        if value is None:
            return None
        value = float(value)
        self.metrics[key] = {"value": value, "unit": unit,
                             "label": label or key}
        shown = f"{key} = {value:.6g}" + (f" {unit}" if unit else "")
        self._add(INFO, shown, detail)
        return value

    def raw(self, key, series, t=None, label=None):
        """Keep a measured series so the report can show it next to the
        checks of the current section - to judge a verdict yourself.

        series: {name: (values, unit)}, all the same length
        t:      timestamps in seconds, or None to plot against the index
        """
        n = max((len(v) for v, _ in series.values()), default=0)
        if not n:
            return
        step = max(1, math.ceil(n / MAX_RAW_POINTS))

        def thin(seq):
            return [float(f"{x:.6g}") for x in seq[::step]]

        self.raw_series[key] = {
            "label": label or key, "section": self._section, "n": n, "step": step,
            "t": thin(t) if t else None,
            "series": {name: {"unit": unit, "values": thin(values)}
                       for name, (values, unit) in series.items()},
        }

    # -- results -----------------------------------------------------------
    def counts(self):
        c = {v: 0 for v in (PASS, FAIL, WARN, SKIP, INFO)}
        for e in self.entries:
            c[e["verdict"]] += 1
        return c

    @property
    def failed(self):
        return self.counts()[FAIL] > 0

    def to_dict(self):
        return {"name": self.name, "started": self.started,
                "duration_s": round(time.time() - self.started, 1),
                "counts": self.counts(), "metrics": self.metrics, "raw": self.raw_series,
                "entries": self.entries}

    def summary(self, prefix=""):
        c = self.counts()
        line = (f"{prefix}{self.name}: {c[PASS]} ok, {c[FAIL]} fehlgeschlagen, "
                f"{c[WARN]} Warnungen, {c[SKIP]} uebersprungen, "
                f"{len(self.metrics)} Messwerte "
                f"({time.time() - self.started:.1f}s)")
        if not self.quiet:
            print("\n" + line, file=sys.stderr)
        return line

    def save(self, path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)

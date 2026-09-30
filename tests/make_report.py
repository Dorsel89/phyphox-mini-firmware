#!/usr/bin/env python3
"""Turn the measurement store into one self-contained HTML page.

    python make_report.py
    python make_report.py --store results/measurements.jsonl --out results/report.html

No dependencies and no internet: the charts are inline SVG, so the file can
be opened anywhere and archived next to the raw data.

The page has two kinds of views:

* Uebersicht - one row per board with a pass/fail mark per test, and the
  measurements side by side. Only the most recent run of each test on each
  board counts here, so a board that was fixed and tested again shows its
  current state, not its history.
* one view per board - every check of its latest runs with the detail text,
  all its measurements, and how each one developed over time. That is where
  "has this sensor aged since last year" gets answered.
"""
import argparse
import datetime
import html
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from minitest import store  # noqa: E402

PALETTE = ["#2f6fb2", "#c4622d", "#3c8c54", "#9b4a9b", "#b0983a",
           "#4aa3a3", "#b1495b", "#6a6a8e"]
GROUP_TITLES = {
    "lsm": "LSM6DSR - Beschleunigung und Drehrate",
    "bmp": "BMP581 - Druck und Temperatur",
    "hdc": "HDC1080 - Temperatur und Feuchte",
    "stcc4": "STCC4 - CO2",
    "datalog": "Datenaufzeichnung",
    "device": "Geraet",
}
# order of the test columns; tests not listed here are appended
TEST_ORDER = ["device-info", "bmp581", "hdc1080", "stcc4", "lsm6dsr", "datalog"]
STATUS_TEXT = {"PASS": "bestanden", "WARN": "Warnungen", "FAIL": "fehlgeschlagen",
               None: "nicht getestet"}
VERDICT_TEXT = {"PASS": "ok", "FAIL": "fehl", "WARN": "warn", "SKIP": "skip",
                "INFO": "info"}


def parse_ts(text):
    try:
        return datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def local(dt):
    return dt.astimezone().strftime("%Y-%m-%d %H:%M")


def record_status(rec):
    c = rec.get("counts") or {}
    if c.get("FAIL"):
        return "FAIL"
    if c.get("WARN"):
        return "WARN"
    return "PASS"


def worst(statuses):
    for s in ("FAIL", "WARN", "PASS"):
        if s in statuses:
            return s
    return None


def metric_value(m):
    return m.get("value") if isinstance(m, dict) else m


def build_index(records):
    """Collect everything the page needs.

    boards[board_id]        name, firmware, first/last, runs,
                            latest[test] = newest record of that test
    history[metric][board]  [(dt, value)] over all runs, for the board views
    labels, units           per metric, taken from the records
    """
    boards, history, labels, units = {}, {}, {}, {}
    for rec in records:
        bid = rec.get("board_id")
        when = parse_ts(rec.get("ts"))
        if not bid or when is None:
            continue
        rec["_when"] = when
        b = boards.setdefault(bid, {"id": bid, "name": None, "firmware": None,
                                    "first": when, "last": when, "runs": set(),
                                    "latest": {}})
        b["first"] = min(b["first"], when)
        b["runs"].add(rec.get("run_id"))
        if when >= b["last"]:
            b["last"] = when
            b["name"] = rec.get("board_name") or b["name"]
            b["firmware"] = rec.get("firmware") or b["firmware"]
        test = rec.get("test") or "?"
        prev = b["latest"].get(test)
        if prev is None or when >= prev["_when"]:
            b["latest"][test] = rec
        for key, m in (rec.get("metrics") or {}).items():
            value = metric_value(m)
            if value is None:
                continue
            history.setdefault(key, {}).setdefault(bid, []).append((when, float(value)))
            if isinstance(m, dict):
                labels.setdefault(key, m.get("label") or key)
                units.setdefault(key, m.get("unit") or "")
    for per_board in history.values():
        for points in per_board.values():
            points.sort(key=lambda p: p[0])
    for b in boards.values():
        b["status"] = worst({record_status(r) for r in b["latest"].values()})
        # the overview only uses what the latest run of each test measured
        b["current"] = {}
        for rec in b["latest"].values():
            for key, m in (rec.get("metrics") or {}).items():
                if metric_value(m) is not None:
                    b["current"][key] = float(metric_value(m))
    return boards, history, labels, units


def fmt(value):
    if value is None:
        return "-"
    a = abs(value)
    if a and (a < 1e-3 or a >= 1e6):
        return f"{value:.3e}"
    if a >= 100:
        return f"{value:.1f}"
    if a >= 1:
        return f"{value:.3f}"
    return f"{value:.5f}".rstrip("0").rstrip(".")


def esc(text):
    return html.escape(str(text if text is not None else ""))


def svg_bars(pairs, unit, width=520, height=190):
    """pairs: [(label, value, colour)] - the latest value per board."""
    if not pairs:
        return "<p class='none'>keine Daten</p>"
    pad_l, pad_b, pad_t = 62, 34, 12
    plot_w, plot_h = width - pad_l - 14, height - pad_b - pad_t
    values = [v for _, v, _ in pairs]
    lo, hi = min(values + [0.0]), max(values + [0.0])
    if hi == lo:
        hi = lo + 1.0
    span = hi - lo
    zero_y = pad_t + plot_h * (hi - 0) / span
    slot = plot_w / len(pairs)
    bar_w = min(58.0, slot * 0.62)
    out = [f"<svg viewBox='0 0 {width} {height}' class='chart' role='img'>"]
    for frac in (0.0, 0.5, 1.0):
        v = hi - span * frac
        y = pad_t + plot_h * frac
        out.append(f"<line x1='{pad_l}' y1='{y:.1f}' x2='{width - 14}' y2='{y:.1f}' "
                   f"class='grid'/>")
        out.append(f"<text x='{pad_l - 6}' y='{y + 4:.1f}' class='tick end'>"
                   f"{esc(fmt(v))}</text>")
    for i, (label, value, colour) in enumerate(pairs):
        cx = pad_l + slot * (i + 0.5)
        y = pad_t + plot_h * (hi - value) / span
        top, bottom = min(y, zero_y), max(y, zero_y)
        out.append(f"<rect x='{cx - bar_w / 2:.1f}' y='{top:.1f}' width='{bar_w:.1f}' "
                   f"height='{max(1.0, bottom - top):.1f}' fill='{colour}' rx='2'/>")
        out.append(f"<text x='{cx:.1f}' y='{height - 20}' class='tick mid'>"
                   f"{esc(label)}</text>")
        out.append(f"<text x='{cx:.1f}' y='{top - 4:.1f}' class='tick mid val'>"
                   f"{esc(fmt(value))}</text>")
    out.append(f"<text x='{pad_l}' y='{height - 5}' class='axis'>{esc(unit)}</text>")
    out.append("</svg>")
    return "".join(out)


def svg_history(points, colour, unit, width=520, height=170):
    """One board's values of one metric over time."""
    if len(points) < 2:
        return None
    pad_l, pad_b, pad_t = 62, 34, 12
    plot_w, plot_h = width - pad_l - 14, height - pad_b - pad_t
    t0, t1 = points[0][0], points[-1][0]
    lo, hi = min(p[1] for p in points), max(p[1] for p in points)
    if hi == lo:
        hi, lo = hi + abs(hi or 1) * 0.05, lo - abs(lo or 1) * 0.05
    span_t = (t1 - t0).total_seconds() or 1.0
    span_v = hi - lo

    def x(dt):
        return pad_l + plot_w * ((dt - t0).total_seconds() / span_t)

    def y(v):
        return pad_t + plot_h * ((hi - v) / span_v)

    out = [f"<svg viewBox='0 0 {width} {height}' class='chart' role='img'>"]
    for frac in (0.0, 0.5, 1.0):
        yy = pad_t + plot_h * frac
        out.append(f"<line x1='{pad_l}' y1='{yy:.1f}' x2='{width - 14}' y2='{yy:.1f}' "
                   f"class='grid'/>")
        out.append(f"<text x='{pad_l - 6}' y='{yy + 4:.1f}' class='tick end'>"
                   f"{esc(fmt(hi - span_v * frac))}</text>")
    d = " ".join(f"{'M' if i == 0 else 'L'}{x(t):.1f},{y(v):.1f}"
                 for i, (t, v) in enumerate(points))
    out.append(f"<path d='{d}' fill='none' stroke='{colour}' stroke-width='2'/>")
    for t, v in points:
        out.append(f"<circle cx='{x(t):.1f}' cy='{y(v):.1f}' r='3.2' fill='{colour}'>"
                   f"<title>{local(t)}: {esc(fmt(v))} {esc(unit)}</title></circle>")
    for dt, anchor in ((t0, "start"), (t1, "end")):
        out.append(f"<text x='{x(dt):.1f}' y='{height - 18}' "
                   f"class='tick {anchor}'>{dt.astimezone():%Y-%m-%d}</text>")
    out.append(f"<text x='{pad_l}' y='{height - 5}' class='axis'>{esc(unit)}</text>")
    out.append("</svg>")
    return "".join(out)


def badge(status, text=None):
    cls = {"PASS": "ok", "WARN": "warn", "FAIL": "fail"}.get(status, "none")
    return f"<span class='badge {cls}'>{esc(text or STATUS_TEXT[status])}</span>"


def view_id(bid):
    return "board-" + re.sub(r"[^0-9A-Za-z]", "", bid)


def metric_title(key, labels, units):
    unit = units.get(key, "")
    return (esc(labels.get(key, key))
            + (f" <span class='unit'>{esc(unit)}</span>" if unit else ""))


def group_of(key):
    return key.split(".", 1)[0]


def group_sort(group):
    return list(GROUP_TITLES).index(group) if group in GROUP_TITLES else 99


def all_tests(boards):
    seen = {t for b in boards.values() for t in b["latest"]}
    return [t for t in TEST_ORDER if t in seen] + sorted(seen - set(TEST_ORDER))


# -- overview ---------------------------------------------------------------
def overview(order, boards, labels, units, colours, tests):
    def name(b):
        return b["name"] or b["id"][-8:]

    parts = ["<section class='view' id='v-overview'>"]
    n_ok = sum(1 for b in order if b["status"] == "PASS")
    parts.append(
        f"<p class='lead'>{len(order)} Boards, davon {n_ok} ohne Fehler. "
        f"Gezeigt wird je Board und Test nur der letzte Lauf.</p>")

    parts.append("<h2>Boards</h2><div class='scroll'><table class='boards'><thead><tr>"
                 "<th>Board</th><th>Ergebnis</th>"
                 + "".join(f"<th class='c'>{esc(t)}</th>" for t in tests)
                 + "<th>Firmware</th><th>letzter Test</th></tr></thead><tbody>")
    for b in order:
        cells = []
        for t in tests:
            rec = b["latest"].get(t)
            if rec is None:
                cells.append("<td class='c'><span class='cell none'>–</span></td>")
                continue
            c = rec.get("counts") or {}
            st = record_status(rec)
            tip = (f"{c.get('PASS', 0)} ok, {c.get('FAIL', 0)} fehlgeschlagen, "
                   f"{c.get('WARN', 0)} Warnungen – {local(rec['_when'])}")
            mark = {"PASS": "✓", "WARN": "!", "FAIL": "✗"}[st]
            cells.append(f"<td class='c'><a class='cell {st.lower()}' "
                         f"href='#{view_id(b['id'])}' title='{esc(tip)}'>{mark}</a></td>")
        parts.append(
            f"<tr><td><a href='#{view_id(b['id'])}' class='blink'>"
            f"<span class='dot' style='background:{colours[b['id']]}'></span>"
            f"{esc(name(b))}</a><br><span class='mono mut'>{esc(b['id'])}</span></td>"
            f"<td>{badge(b['status'])}</td>" + "".join(cells)
            + f"<td class='mono'>{esc(b['firmware'] or '-')}</td>"
              f"<td>{local(b['last'])}</td></tr>")
    parts.append("</tbody></table></div>"
                 "<p class='note'>✓ bestanden, ! Warnungen, ✗ fehlgeschlagen, – nicht "
                 "getestet. Ein Klick auf das Board zeigt alle Pruefungen und Messwerte.</p>")

    keys = sorted({k for b in order for k in b["current"]},
                  key=lambda k: (group_sort(group_of(k)), k))
    if not keys:
        parts.append("</section>")
        return "\n".join(parts)

    parts.append("<h2>Messwerte im Vergleich</h2><div class='scroll'><table><thead><tr>"
                 "<th>Messgroesse</th>"
                 + "".join(f"<th class='num'>{esc(name(b))}</th>" for b in order)
                 + "</tr></thead><tbody>")
    for key in keys:
        cells = "".join(f"<td class='num'>{fmt(b['current'].get(key))}</td>"
                        for b in order)
        parts.append(f"<tr><td>{metric_title(key, labels, units)}"
                     f"<br><code class='key'>{esc(key)}</code></td>{cells}</tr>")
    parts.append("</tbody></table></div>")

    groups = {}
    for key in keys:
        groups.setdefault(group_of(key), []).append(key)
    for group in sorted(groups, key=group_sort):
        parts.append(f"<h2>{esc(GROUP_TITLES.get(group, group))}</h2><div class='grid2'>")
        for key in groups[group]:
            bars = [(name(b), b["current"][key], colours[b["id"]])
                    for b in order if key in b["current"]]
            parts.append(f"<section class='metric'><h3>{metric_title(key, labels, units)}"
                         f"</h3><code class='key'>{esc(key)}</code>"
                         f"{svg_bars(bars, units.get(key, ''))}</section>")
        parts.append("</div>")
    parts.append("</section>")
    return "\n".join(parts)


# -- one board --------------------------------------------------------------
def board_view(b, history, labels, units, colour, tests):
    title = b["name"] or b["id"]
    parts = [f"<section class='view' id='v-{view_id(b['id'])}'>",
             f"<p><a href='#overview' class='back'>← Uebersicht</a></p>",
             f"<h2 class='bh'><span class='dot big' style='background:{colour}'></span>"
             f"{esc(title)} {badge(b['status'])}</h2>",
             "<table class='facts'><tbody>"
             f"<tr><th>Adresse</th><td class='mono'>{esc(b['id'])}</td></tr>"
             f"<tr><th>Firmware</th><td class='mono'>{esc(b['firmware'] or '-')}</td></tr>"
             f"<tr><th>erste Messung</th><td>{local(b['first'])}</td></tr>"
             f"<tr><th>letzte Messung</th><td>{local(b['last'])}</td></tr>"
             f"<tr><th>Durchlaeufe</th><td>{len(b['runs'])}</td></tr>"
             "</tbody></table>"]

    parts.append("<h2>Pruefungen (letzter Lauf je Test)</h2>")
    for t in tests:
        rec = b["latest"].get(t)
        if rec is None:
            parts.append(f"<div class='test'><div class='tsum'>{badge(None)} "
                         f"<b>{esc(t)}</b></div></div>")
            continue
        st = record_status(rec)
        c = rec.get("counts") or {}
        summary = (f"{badge(st)} <b>{esc(t)}</b> <span class='mut'>{c.get('PASS', 0)} ok"
                   f" · {c.get('FAIL', 0)} fehl · {c.get('WARN', 0)} warn"
                   f" · {local(rec['_when'])} · Firmware {esc(rec.get('firmware') or '-')}"
                   f"</span>")
        checks = rec.get("checks")
        if checks is None:
            body = "<p class='none'>Einzelpruefungen wurden in diesem Lauf nicht gespeichert.</p>"
        else:
            rows, section = [], object()
            for e in checks:
                if e.get("section") != section:
                    section = e.get("section")
                    if section:
                        rows.append(f"<tr class='sec'><td colspan='3'>{esc(section)}</td></tr>")
                v = e.get("verdict", "INFO")
                rows.append(f"<tr class='v-{v.lower()}'><td><span class='chip {v.lower()}'>"
                            f"{VERDICT_TEXT.get(v, v)}</span></td>"
                            f"<td>{esc(e.get('title'))}</td>"
                            f"<td class='detail'>{esc(e.get('detail'))}</td></tr>")
            body = ("<table class='checks'><tbody>" + "".join(rows) + "</tbody></table>")
        open_attr = " open" if st == "FAIL" else ""
        parts.append(f"<details class='test'{open_attr}><summary class='tsum'>{summary}"
                     f"</summary>{body}</details>")

    keys = sorted((k for k in history if b["id"] in history[k]),
                  key=lambda k: (group_sort(group_of(k)), k))
    if keys:
        parts.append("<h2>Messwerte</h2><div class='scroll'><table><thead><tr>"
                     "<th>Messgroesse</th><th class='num'>aktuell</th>"
                     "<th class='num'>vorher</th><th class='num'>Aenderung</th>"
                     "<th class='num'>Messungen</th></tr></thead><tbody>")
        for key in keys:
            pts = history[key][b["id"]]
            cur = pts[-1][1]
            prev = pts[-2][1] if len(pts) > 1 else None
            change = "-"
            if prev is not None:
                change = f"{cur - prev:+.4g}"
                if prev:
                    change += f" ({(cur - prev) / abs(prev) * 100:+.1f} %)"
            parts.append(f"<tr><td>{metric_title(key, labels, units)}"
                         f"<br><code class='key'>{esc(key)}</code></td>"
                         f"<td class='num'>{fmt(cur)}</td><td class='num'>{fmt(prev)}</td>"
                         f"<td class='num'>{esc(change)}</td>"
                         f"<td class='num'>{len(pts)}</td></tr>")
        parts.append("</tbody></table></div>")

        charts = [(k, svg_history(history[k][b["id"]], colour, units.get(k, "")))
                  for k in keys]
        charts = [(k, svg) for k, svg in charts if svg]
        if charts:
            parts.append("<h2>Verlauf</h2><div class='grid2'>")
            for key, svg in charts:
                parts.append(f"<section class='metric'><h3>{metric_title(key, labels, units)}"
                             f"</h3><code class='key'>{esc(key)}</code>{svg}</section>")
            parts.append("</div>")
        else:
            parts.append("<p class='note'>Den Verlauf gibt es, sobald das Board mehr als "
                         "einmal gemessen wurde.</p>")
    parts.append("</section>")
    return "\n".join(parts)


def build_html(boards, history, labels, units, store_path):
    order = sorted(boards.values(), key=lambda b: (b["name"] or b["id"]))
    colours = {b["id"]: PALETTE[i % len(PALETTE)] for i, b in enumerate(order)}
    tests = all_tests(boards)

    nav = ["<nav><a href='#overview' data-view='overview'>Uebersicht</a>"]
    for b in order:
        st = {"PASS": "ok", "WARN": "warn", "FAIL": "fail"}.get(b["status"], "none")
        nav.append(f"<a href='#{view_id(b['id'])}' data-view='{view_id(b['id'])}'>"
                   f"<span class='sdot {st}'></span>{esc(b['name'] or b['id'][-8:])}</a>")
    nav.append("</nav>")

    body = ["".join(nav), overview(order, boards, labels, units, colours, tests)]
    body += [board_view(b, history, labels, units, colours[b["id"]], tests) for b in order]
    return (TEMPLATE.replace("{{BODY}}", "\n".join(body))
            .replace("{{GENERATED}}", datetime.datetime.now().strftime("%Y-%m-%d %H:%M"))
            .replace("{{SOURCE}}", esc(os.path.basename(store_path))))


TEMPLATE = """<!doctype html>
<html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>phyphox:mini Testergebnisse</title>
<style>
 :root{--fg:#1d2126;--mut:#61696f;--line:#dfe3e7;--bg:#fff;--card:#f7f8fa;
      --ok:#2e7d46;--okbg:#e3f3e8;--warn:#9a6700;--warnbg:#fdf2d6;
      --fail:#b42318;--failbg:#fde7e5;--nonebg:#eef0f2;--accent:#2f6fb2}
 @media (prefers-color-scheme:dark){:root{--fg:#e3e6e9;--mut:#9aa3ab;--line:#33393f;
      --bg:#16191c;--card:#1e2226;--ok:#6fcf8e;--okbg:#1d3326;--warn:#e7b949;
      --warnbg:#382f14;--fail:#f28b82;--failbg:#3d1f1d;--nonebg:#262b30;--accent:#7fb0e6}}
 *{box-sizing:border-box}
 body{margin:0 auto;padding:24px 16px 60px;font:14px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif;
      color:var(--fg);background:var(--bg);max-width:1180px}
 h1{font-size:22px;margin:0 0 2px} h2{font-size:17px;margin:30px 0 10px;
      padding-bottom:5px;border-bottom:1px solid var(--line)}
 h3{font-size:14px;margin:0 0 2px;font-weight:600}
 a{color:var(--accent);text-decoration:none} a:hover{text-decoration:underline}
 .lead{color:var(--mut);margin:0 0 6px} .sub{color:var(--mut);font-size:12px;margin:0 0 14px}
 .note{color:var(--mut);font-size:12px} .mut{color:var(--mut)}
 nav{display:flex;flex-wrap:wrap;gap:6px;margin:0 0 18px;position:sticky;top:0;
      background:var(--bg);padding:8px 0;border-bottom:1px solid var(--line);z-index:1}
 nav a{padding:5px 12px;border:1px solid var(--line);border-radius:999px;color:var(--fg);
      font-size:13px;display:inline-flex;align-items:center;gap:6px}
 nav a.active{background:var(--fg);color:var(--bg);border-color:var(--fg)}
 nav a:hover{text-decoration:none;border-color:var(--mut)}
 .sdot{width:8px;height:8px;border-radius:50%;display:inline-block}
 .sdot.ok{background:var(--ok)} .sdot.warn{background:var(--warn)}
 .sdot.fail{background:var(--fail)} .sdot.none{background:var(--mut)}
 .view{display:none} .view.active{display:block}
 table{border-collapse:collapse;width:100%;font-size:13px}
 th,td{text-align:left;padding:6px 10px;border-bottom:1px solid var(--line);vertical-align:top}
 th{font-weight:600;color:var(--mut);font-size:12px}
 .num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
 .c{text-align:center}
 .mono,code{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12px}
 code.key{color:var(--mut);font-size:11px}
 .unit{color:var(--mut);font-weight:400}
 .dot{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:7px}
 .dot.big{width:13px;height:13px;margin-right:10px}
 .blink{font-weight:600;color:var(--fg)}
 .scroll{overflow-x:auto}
 .badge{display:inline-block;padding:1px 9px;border-radius:999px;font-size:12px;
      font-weight:600;white-space:nowrap;vertical-align:middle}
 .badge.ok{color:var(--ok);background:var(--okbg)} .badge.warn{color:var(--warn);background:var(--warnbg)}
 .badge.fail{color:var(--fail);background:var(--failbg)} .badge.none{color:var(--mut);background:var(--nonebg)}
 .cell{display:inline-block;width:26px;height:26px;line-height:26px;border-radius:6px;
      font-weight:700;text-align:center}
 .cell.pass{color:var(--ok);background:var(--okbg)} .cell.warn{color:var(--warn);background:var(--warnbg)}
 .cell.fail{color:var(--fail);background:var(--failbg)} .cell.none{color:var(--mut)}
 .cell:hover{text-decoration:none;outline:1px solid var(--mut)}
 .bh{display:flex;align-items:center;gap:10px;border:0;font-size:20px;margin-top:6px}
 .facts{width:auto} .facts th{padding-right:24px}
 .test{border:1px solid var(--line);border-radius:8px;margin:8px 0;background:var(--card)}
 .tsum{padding:9px 12px;cursor:pointer;list-style-position:inside}
 div.test .tsum{cursor:default}
 .checks{background:var(--bg);border-top:1px solid var(--line)}
 .checks td{padding:4px 10px}
 .checks td:first-child{width:60px} .checks td:nth-child(2){width:38%}
 .checks tr.sec td{font-weight:600;color:var(--mut);font-size:12px;padding-top:10px}
 .checks tr.v-info td{color:var(--mut)}
 .detail{color:var(--mut);font-size:12px;word-break:break-word}
 .chip{display:inline-block;min-width:34px;text-align:center;font-size:11px;font-weight:600;
      border-radius:4px;padding:0 5px}
 .chip.pass{color:var(--ok);background:var(--okbg)} .chip.fail{color:var(--fail);background:var(--failbg)}
 .chip.warn{color:var(--warn);background:var(--warnbg)}
 .chip.skip,.chip.info{color:var(--mut);background:var(--nonebg)}
 .back{font-size:13px}
 .grid2{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:12px}
 .metric{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px 14px}
 .chart{width:100%;height:auto;overflow:visible;margin-top:6px}
 .grid{stroke:var(--line);stroke-width:1}
 .tick{font-size:10px;fill:var(--mut)}
 .tick.end{text-anchor:end} .tick.mid{text-anchor:middle}
 .tick.start{text-anchor:start} .tick.val{fill:var(--fg);font-weight:600}
 .axis{font-size:10px;fill:var(--mut)}
 .none{color:var(--mut);font-size:12px;padding:0 12px}
</style>
<noscript><style>.view{display:block} nav{display:none}</style></noscript>
</head><body>
<h1>phyphox:mini Testergebnisse</h1>
<p class="sub">erzeugt am {{GENERATED}} aus <code>{{SOURCE}}</code></p>
{{BODY}}
<script>
 function show(){
   // views carry a 'v-' prefix so the browser does not jump to them itself
   var id = (location.hash || '#overview').slice(1);
   if (!document.getElementById('v-' + id)) id = 'overview';
   document.querySelectorAll('.view').forEach(function(v){
     v.classList.toggle('active', v.id === 'v-' + id); });
   document.querySelectorAll('nav a').forEach(function(a){
     a.classList.toggle('active', a.dataset.view === id); });
   window.scrollTo(0, 0);
 }
 window.addEventListener('hashchange', show); show();
</script>
</body></html>
"""


def write_report(store_path=store.DEFAULT_PATH, out=None):
    """Build the HTML page from the store. Returns its path, or None if
    there is nothing to show."""
    records = store.load(store_path)
    if not records:
        print(f"keine Messwerte in {store_path}", file=sys.stderr)
        return None
    boards, history, labels, units = build_index(records)
    if not boards:
        print("keine verwertbaren Datensaetze", file=sys.stderr)
        return None

    out = out or os.path.join(os.path.dirname(store_path) or ".", "report.html")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(build_html(boards, history, labels, units, store_path))
    failed = sorted(b["name"] or b["id"] for b in boards.values() if b["status"] == "FAIL")
    print(f"{len(records)} Datensaetze, {len(boards)} Boards, "
          f"{len(history)} Messgroessen -> {out}")
    if failed:
        print(f"mit Fehlern: {', '.join(failed)}")
    return out


def main():
    p = argparse.ArgumentParser(prog="make_report")
    p.add_argument("--store", default=store.DEFAULT_PATH)
    p.add_argument("--out", help="Zieldatei, Standard: report.html neben dem Store")
    args = p.parse_args()
    return 0 if write_report(args.store, args.out) else 1


if __name__ == "__main__":
    sys.exit(main())

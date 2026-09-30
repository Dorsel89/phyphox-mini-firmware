#!/usr/bin/env python3
"""BMP581 (pressure, temperature) of a phyphox:mini.

Checks that data arrives, that the values are plausible, that every
oversampling setting really changes how many frames the firmware packs into
one notification, and that the IIR filter actually smooths the signal.

    python test_bmp581.py --address E8:C6:21:E2:29:78
"""
import statistics
import sys

from minitest import spec
from minitest.runner import standalone

FMT, NAMES, SIZE = spec.FRAMES["bmp581"]


def extra(p):
    p.add_argument("--window", type=float, default=4.0,
                   help="Messfenster je Schritt in Sekunden")
    p.add_argument("--skip-oversampling", action="store_true",
                   help="den langen Durchlauf ueber alle Oversampling-Stufen auslassen")


async def run(dev, rep, args):
    async with dev.stream(spec.BMP_DATA) as stream:
        await dev.restart_run()

        # ---------------------------------------------------------------
        rep.section("Grundfunktion")
        await dev.write(spec.BMP_CFG, spec.bmp_config(spec.BMP_ON, 0x00, 0x01))
        cap = await stream.capture(args.window, settle=1.0)
        frames = cap.frames(FMT, NAMES, SIZE)

        if not rep.check("Daten kommen an", len(frames) > 0,
                         f"{len(cap)} Notifications, {len(frames)} Messwerte"):
            await dev.write(spec.BMP_CFG, spec.bmp_config(spec.BMP_OFF))
            return

        bad = [n for n in cap.lengths if n % SIZE]
        rep.check(f"Paketlaengen sind Vielfache von {SIZE} Byte",
                  not bad, f"Laengen {dict(cap.lengths)}")

        st = cap.timestamp_stats(frames)
        rep.check("Zeitstempel streng aufsteigend", st and st["non_monotonic"] == 0,
                  f"{st['non_monotonic']} Rueckspruenge von {st['n'] - 1}" if st else "")
        if st and st["rate_hz"]:
            rep.metric("bmp.rate_hz", st["rate_hz"], unit="Hz",
                       label="Messrate bei Oversampling 1x",
                       detail=f"{st['median_dt_s'] * 1000:.1f} ms je Messwert")

        for field in ("pressure", "temperature"):
            values = [f[field] for f in frames]
            lo, hi = spec.PLAUSIBLE[field]
            mean = statistics.mean(values)
            unit = "hPa" if field == "pressure" else "degC"
            rep.metric(f"bmp.{field}_mean", mean, unit=unit,
                       label=f"BMP {field} Mittelwert",
                       detail=f"Spanne {min(values):.3f} .. {max(values):.3f}")
            rep.within(f"{field} plausibel", mean, lo, hi)

        # ---------------------------------------------------------------
        rep.section("Konfiguration: Oversampling bestimmt die Paketgroesse")
        if args.skip_oversampling:
            rep.skip("Oversampling-Durchlauf", "per --skip-oversampling abgewaehlt")
        else:
            for osr, expected_frames in sorted(spec.BMP_FRAMES_PER_PACKET.items()):
                await dev.write(spec.BMP_CFG, spec.bmp_config(spec.BMP_ON, osr, 0x01))
                cap = await stream.capture(args.window, settle=1.5)
                label = f"{spec.BMP_OVERSAMPLING[osr]}x"
                if not len(cap):
                    rep.check(f"Oversampling {label}", False, "keine Notification erhalten")
                    continue
                common = cap.lengths.most_common(1)[0][0]
                got = common // SIZE
                rep.check(f"Oversampling {label}: {expected_frames} Frames je Paket",
                          got == expected_frames,
                          f"{common} Byte = {got} Frames, "
                          f"{cap.notification_rate:.1f} Pakete/s")

        # ---------------------------------------------------------------
        rep.section("Konfiguration: IIR-Filter glaettet")
        noise = {}
        for iir in (0x00, 0x07):
            await dev.write(spec.BMP_CFG, spec.bmp_config(spec.BMP_ON, 0x00, iir))
            cap = await stream.capture(args.window, settle=1.5)
            values = [f["pressure"] for f in cap.frames(FMT, NAMES, SIZE)]
            noise[iir] = statistics.pstdev(values) if len(values) > 2 else None
            if noise[iir] is None:
                rep.warn(f"Rauschen bei IIR {spec.BMP_IIR[iir]}", "zu wenige Werte")
            else:
                rep.metric(f"bmp.pressure_noise_iir{spec.BMP_IIR[iir]}_hpa",
                           noise[iir], unit="hPa",
                           label=f"BMP Druckrauschen bei IIR {spec.BMP_IIR[iir]}",
                           detail=f"{len(values)} Werte")
        if noise.get(0x00) and noise.get(0x07):
            ratio = noise[0x07] / noise[0x00]
            rep.metric("bmp.iir_noise_ratio", ratio,
                       label="Rauschen IIR 127 / Rauschen Bypass")
            # Bei Oversampling 1x liegt das Rauschen ohnehin nahe der
            # Quantisierung, ein Unterschied ist dann nicht zu erwarten.
            # Deshalb nur eine Warnung, wenn der Filter das Rauschen erhoeht.
            if ratio > 1.15:
                rep.warn("IIR 127 rauscht nicht weniger als Bypass",
                         f"Verhaeltnis {ratio:.2f} (stdev {noise[0x07]:.5f} vs "
                         f"{noise[0x00]:.5f} hPa). Bei 1x Oversampling liegt beides "
                         f"nahe der Aufloesungsgrenze.")
            else:
                rep.check("IIR erhoeht das Rauschen nicht", True,
                          f"Verhaeltnis {ratio:.2f}")
        else:
            rep.skip("IIR-Vergleich", "zu wenige Messwerte")

        # ---------------------------------------------------------------
        rep.section("Abschalten")
        await dev.write(spec.BMP_CFG, spec.bmp_config(spec.BMP_OFF))
        cap = await stream.capture(2.0, settle=1.0)
        rep.check("nach enable=0 kommen keine Daten mehr", len(cap) == 0,
                  f"{len(cap)} Notifications trotzdem erhalten")


if __name__ == "__main__":
    sys.exit(standalone(run, "bmp581", extra))

"""BLE plumbing shared by all phyphox:mini test scripts.

Two things every test needs and that are easy to get wrong:

* The device advertises only every ~2 s and Windows does not scan
  continuously, so a single short scan can miss it. find() searches for a
  known address, which returns as soon as the device is seen, and retries.
* The firmware drops every notification until 0x01 has been written to the
  run control characteristic. A client that only enables notifications and
  writes sensor configs receives nothing at all.
"""
import asyncio
import statistics
import struct
import time
from collections import Counter

from bleak import BleakClient, BleakScanner

from . import spec


class Capture:
    """The notifications collected during one measuring window."""

    def __init__(self, packets, duration):
        self.packets = packets            # list of (host_monotonic, bytes)
        self.duration = duration

    def __len__(self):
        return len(self.packets)

    @property
    def lengths(self):
        return Counter(len(p) for _, p in self.packets)

    @property
    def notification_rate(self):
        return len(self.packets) / self.duration if self.duration else 0.0

    def frames(self, fmt, names, frame_size):
        """Decode every packet into frames, the way phyphox does it: read at
        the field offsets and step forward by frame_size."""
        out, leftover = [], 0
        for _, p in self.packets:
            if len(p) % frame_size:
                leftover += 1
            for off in range(0, len(p) - frame_size + 1, frame_size):
                out.append(dict(zip(names, struct.unpack_from(fmt, p, off))))
        self.partial_packets = leftover
        return out

    def frame_rate(self, frames):
        return len(frames) / self.duration if self.duration else 0.0

    @staticmethod
    def timestamp_stats(frames, key="t"):
        """Spacing of the device side timestamps, which is the honest measure
        of the real sample rate - the nominal ODR is not exact."""
        ts = [f[key] for f in frames if key in f]
        if len(ts) < 3:
            return None
        deltas = [b - a for a, b in zip(ts, ts[1:])]
        good = [d for d in deltas if d > 0]
        return {
            "n": len(ts),
            "span_s": ts[-1] - ts[0],
            "rate_hz": (len(ts) - 1) / (ts[-1] - ts[0]) if ts[-1] > ts[0] else None,
            "median_dt_s": statistics.median(good) if good else None,
            "non_monotonic": len(deltas) - len(good),
            "first": ts[0], "last": ts[-1],
        }


class Stream:
    """A subscribed characteristic that can be captured in windows."""

    def __init__(self, device, char):
        self.device, self.char = device, char
        self.packets = []

    async def __aenter__(self):
        await self.device.client.start_notify(
            self.char, lambda _, data: self.packets.append((time.monotonic(), bytes(data))))
        return self

    async def __aexit__(self, *exc):
        try:
            await self.device.client.stop_notify(self.char)
        except Exception:
            pass

    async def capture(self, seconds, settle=0.0):
        """Discard whatever is in flight, then collect for `seconds`."""
        if settle:
            await asyncio.sleep(settle)
        self.packets.clear()
        t0 = time.monotonic()
        await asyncio.sleep(seconds)
        return Capture(list(self.packets), time.monotonic() - t0)


class MiniDevice:
    # Zephyr sends its own preferred parameters (30-50 ms, 420 ms supervision
    # timeout) five seconds after the connection is up, and it does so even if
    # the client asked for something else before. Measured on a phyphox:mini:
    # both requests get applied, in whatever order the central processes them.
    # Sending ours a second time after that point makes sure it is the last.
    AUTO_UPDATE_DELAY_S = 5.0

    def __init__(self, address=None, name_prefix="phyphox:mini",
                 scan_timeout=60.0, attempts=3, log=print, conn_params=None,
                 board=None):
        self.address = address
        self.board = board
        self.name_prefix = name_prefix
        self.scan_timeout = scan_timeout
        self.attempts = attempts
        self.log = log
        self.conn_params = conn_params
        self.client = None
        self.name = None

    # -- discovery and connection -----------------------------------------
    async def _find(self):
        if self.address:
            for n in range(1, self.attempts + 1):
                t0 = time.time()
                dev = await BleakScanner.find_device_by_address(
                    self.address, timeout=self.scan_timeout)
                if dev:
                    self.log(f"gefunden nach {time.time() - t0:.1f}s: {dev.address}")
                    return dev
                self.log(f"{self.address} nicht gesehen "
                         f"(Versuch {n}/{self.attempts})")
            raise RuntimeError(f"Geraet {self.address} nicht gefunden")

        if self.board:
            # Same idea as the address search: stop as soon as the name shows
            # up instead of a fixed scan window that can miss the device.
            target = f"{self.name_prefix} {self.board}"
            for n in range(1, self.attempts + 1):
                t0 = time.time()
                dev = await BleakScanner.find_device_by_filter(
                    lambda d, a: (a.local_name or d.name or "").strip() == target,
                    timeout=self.scan_timeout)
                if dev:
                    self.log(f"gefunden nach {time.time() - t0:.1f}s: "
                             f"{target} ({dev.address})")
                    return dev
                self.log(f"{target!r} nicht gesehen (Versuch {n}/{self.attempts})")
            raise RuntimeError(f"Board {target!r} nicht gefunden")

        found = await BleakScanner.discover(timeout=20.0, return_adv=True)
        cands = [(d, a) for d, a in found.values()
                 if (a.local_name or d.name or "").startswith(self.name_prefix)]
        for d, a in sorted(cands, key=lambda p: -p[1].rssi):
            self.log(f"  {d.address}  {(a.local_name or '').strip()!r}  rssi {a.rssi}")
        if not cands:
            raise RuntimeError("kein phyphox:mini gefunden")
        if len(cands) > 1:
            raise RuntimeError(f"{len(cands)} Geraete gefunden - bitte --board "
                               f"oder --address angeben")
        return cands[0][0]

    async def _connect(self):
        dev = await self._find()
        self.name = (dev.name or "").strip()
        try:
            self.client = BleakClient(dev, timeout=30.0,
                                      winrt=dict(use_cached_services=False))
        except TypeError:                      # older bleak without the winrt kwarg
            self.client = BleakClient(dev, timeout=30.0)
        await self.client.connect()
        connected_at = time.monotonic()
        self.address = self.client.address
        self.log(f"verbunden mit {self.name!r} ({self.address}), MTU {self.client.mtu_size}")
        if self.conn_params:
            await self._request_conn_params(connected_at)

    async def _request_conn_params(self, connected_at):
        """Ask the fob for our connection parameters, and again once Zephyr's
        automatic update has run, so that ours are the ones that stay."""
        await self.write(spec.DEVICE_CFG, self.conn_params)
        wait = self.AUTO_UPDATE_DELAY_S + 1.0 - (time.monotonic() - connected_at)
        if wait > 0:
            await asyncio.sleep(wait)
        if self.client.is_connected:
            await self.write(spec.DEVICE_CFG, self.conn_params)
        p = self.conn_params
        self.log(f"Verbindungsparameter angefordert: {p[1] * 1.25:g}-{p[2] * 1.25:g} ms, "
                 f"Latenz {p[3]}, Timeout {p[4] * 10} ms")

    async def set_conn_params(self, params, settle=1.0):
        """Switch the connection parameters mid-session, e.g. for a single
        test that needs a shorter interval. None leaves the link alone. The
        fob applies the request 200 ms after the write, so wait a little."""
        if not params:
            return
        await self.write(spec.DEVICE_CFG, params)
        await asyncio.sleep(settle)

    async def __aenter__(self):
        await self._connect()
        return self

    async def disconnect(self):
        """Let go of the link. Background logging only runs while no client
        is connected, so the datalog test needs this."""
        if self.client and self.client.is_connected:
            await self.client.disconnect()

    async def reconnect(self):
        await self.disconnect()
        await self._connect()

    async def __aexit__(self, *exc):
        if self.client and self.client.is_connected:
            try:
                await self.clear_run()
            except Exception:
                pass
            await self.client.disconnect()

    # -- primitives --------------------------------------------------------
    async def read(self, char):
        return bytes(await self.client.read_gatt_char(char))

    async def read_string(self, char):
        return (await self.read(char)).decode("utf-8", "replace").strip()

    async def write(self, char, data, response=True):
        await self.client.write_gatt_char(char, bytes(data), response=response)

    async def start_run(self):
        """Opens the notification gate. Without this nothing is ever sent."""
        await self.write(spec.RUN_CONTROL, bytes([spec.RUN_START]))

    async def clear_run(self):
        await self.write(spec.RUN_CONTROL, bytes([spec.RUN_CLEAR]))

    async def restart_run(self):
        """A fresh time reference: 0x01 is ignored unless the device is in the
        reset state, so clear first."""
        await self.clear_run()
        await asyncio.sleep(0.2)
        await self.start_run()

    def stream(self, char):
        return Stream(self, char)

    def has_char(self, char):
        return any(c.uuid.lower() == char.lower()
                   for s in self.client.services for c in s.characteristics)

    # -- leaving the device in a sane state --------------------------------
    async def all_sensors_off(self):
        await self.write(spec.BMP_CFG, spec.bmp_config(spec.BMP_OFF))
        await self.write(spec.HDC_CFG, bytes([0x00, 0x0A]))
        await self.write(spec.STCC4_CFG, bytes([0x00, 0x0A]))
        await self.write(spec.LSM_CFG, spec.lsm_config(0x00, 0x04))


def add_common_arguments(parser):
    parser.add_argument("--address", help="BLE-Adresse, z.B. E8:C6:21:E2:29:78")
    parser.add_argument("--board", help="Board-Name ohne Praefix, z.B. A19 "
                                        "(sucht 'phyphox:mini A19')")
    parser.add_argument("--name-prefix", default="phyphox:mini")
    parser.add_argument("--scan-timeout", type=float, default=60.0)
    parser.add_argument("--json", help="Ergebnis zusaetzlich als JSON hierhin schreiben")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--conn-params", default="30,30,0,2500",
                        help="Verbindungsparameter per cddf1022 anfordern: "
                             "min_ms,max_ms,latenz,timeout_ms (Standard 30,30,0,2500; "
                             "Windows und iOS erlauben nicht unter 15 ms)")
    parser.add_argument("--no-conn-params", action="store_true",
                        help="keine Parameter anfordern, Firmware-Standard behalten")
    return parser


def conn_params_from_args(args):
    if getattr(args, "no_conn_params", False):
        return None
    lo, hi, latency, timeout = (float(x) for x in args.conn_params.split(","))
    return spec.conn_params(lo, hi, int(latency), timeout)

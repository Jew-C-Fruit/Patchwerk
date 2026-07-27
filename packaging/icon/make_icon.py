"""A PLACEHOLDER Patchwerk icon, drawn from stdlib only.

Cole said a placeholder icon is fine, so this spends no time on artwork and
no dependency on Pillow: it rasterises a few rounded rectangles and two
wires — cards in a gutter, which is at least what the product looks like —
supersampled 4x for smooth edges, and writes a PNG by hand with `zlib`.

    python3 packaging/icon/make_icon.py OUTDIR

writes `icon_<size>.png` for every size the platforms want, plus
`Patchwerk.ico` (Windows; an ICO is a header plus embedded PNGs). The
`.icns` is made by `iconutil`, which is part of macOS — see build.py.

Replace this wholesale when there is real artwork; nothing else imports it.
"""

from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path

def _ss(size: int) -> int:
    """Supersample factor, capped so the work stays bounded.

    This is a pure-Python rasteriser, so cost is O((size*SS)^2) and a flat
    SS=4 spent two minutes on the 1024 icon alone. Small icons are where
    aliasing actually shows, so they keep the full 4x; past 256 the shapes
    are large enough that hard edges are invisible and 1x is fine.
    """
    return 4 if size <= 128 else (2 if size <= 256 else 1)

BG = (0x14, 0x16, 0x1c)
EDGE = (0x2a, 0x30, 0x3d)
CARDS = [                    # roughly the GUI's category colours
    (0x4d, 0x7c, 0xfe),
    (0x3f, 0xba, 0x8f),
    (0xd9, 0xa1, 0x3b),
    (0xc8, 0x6b, 0xd9),
]
WIRE = (0x7f, 0x8a, 0xa3)

ICNS_SIZES = [16, 32, 64, 128, 256, 512, 1024]
ICO_SIZES = [16, 24, 32, 48, 64, 128, 256]


# -- tiny raster --------------------------------------------------------------

class Canvas:
    def __init__(self, n: int) -> None:
        self.n = n
        self.px = bytearray(n * n * 4)

    def blend(self, x: int, y: int, rgb, a: float) -> None:
        if not (0 <= x < self.n and 0 <= y < self.n) or a <= 0:
            return
        i = (y * self.n + x) * 4
        p = self.px
        ia = 1.0 - a
        for k in range(3):
            p[i + k] = int(p[i + k] * ia + rgb[k] * a)
        p[i + 3] = min(255, int(p[i + 3] * ia + 255 * a))

    def rrect(self, x0, y0, x1, y1, r, rgb, a=1.0) -> None:
        for y in range(int(y0), int(y1) + 1):
            for x in range(int(x0), int(x1) + 1):
                # inside the rect minus the four corner circles
                cx = min(max(x, x0 + r), x1 - r)
                cy = min(max(y, y0 + r), y1 - r)
                if (x - cx) ** 2 + (y - cy) ** 2 <= r * r:
                    self.blend(x, y, rgb, a)

    def line(self, x0, y0, x1, y1, w, rgb) -> None:
        steps = int(max(abs(x1 - x0), abs(y1 - y0))) * 2 + 1
        for s in range(steps + 1):
            t = s / steps
            x = x0 + (x1 - x0) * t
            y = y0 + (y1 - y0) * t
            self.rrect(x - w / 2, y - w / 2, x + w / 2, y + w / 2, w / 2, rgb)

    def downsample(self, factor: int) -> "Canvas":
        out = Canvas(self.n // factor)
        f2 = factor * factor
        for y in range(out.n):
            for x in range(out.n):
                acc = [0, 0, 0, 0]
                for dy in range(factor):
                    row = ((y * factor + dy) * self.n + x * factor) * 4
                    for dx in range(factor):
                        i = row + dx * 4
                        for k in range(4):
                            acc[k] += self.px[i + k]
                o = (y * out.n + x) * 4
                for k in range(4):
                    out.px[o + k] = acc[k] // f2
        return out

    def png(self) -> bytes:
        raw = b"".join(
            b"\x00" + bytes(self.px[y * self.n * 4:(y + 1) * self.n * 4])
            for y in range(self.n)
        )

        def chunk(tag: bytes, data: bytes) -> bytes:
            return (struct.pack(">I", len(data)) + tag + data
                    + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

        return (b"\x89PNG\r\n\x1a\n"
                + chunk(b"IHDR", struct.pack(">IIBBBBB", self.n, self.n,
                                             8, 6, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw, 9))
                + chunk(b"IEND", b""))


def draw(size: int) -> Canvas:
    """Four cards on a dark tile, two of them wired together."""
    ss = _ss(size)
    n = size * ss
    c = Canvas(n)
    u = n / 100.0                      # work in percent-of-icon units

    c.rrect(4 * u, 4 * u, 96 * u, 96 * u, 22 * u, EDGE)
    c.rrect(6 * u, 6 * u, 94 * u, 94 * u, 20 * u, BG)

    boxes = [(18, 20, 44, 40), (56, 20, 82, 40),
             (18, 58, 44, 78), (56, 58, 82, 78)]
    # wires first, so the cards sit on top of their ends
    c.line(44 * u, 30 * u, 56 * u, 30 * u, 3.0 * u, WIRE)
    c.line(31 * u, 40 * u, 31 * u, 58 * u, 3.0 * u, WIRE)
    c.line(69 * u, 40 * u, 69 * u, 58 * u, 3.0 * u, WIRE)
    c.line(44 * u, 68 * u, 56 * u, 68 * u, 3.0 * u, WIRE)

    for (x0, y0, x1, y1), col in zip(boxes, CARDS):
        c.rrect(x0 * u, y0 * u, x1 * u, y1 * u, 4 * u, col)
        # the colour bar along the top edge, filled = powered
        c.rrect(x0 * u, y0 * u, x1 * u, (y0 + 4) * u, 2 * u,
                (255, 255, 255), 0.55)

    return c.downsample(ss) if ss > 1 else c


# -- containers ---------------------------------------------------------------

def write_ico(pngs: dict[int, bytes], path: Path) -> None:
    """An ICO is a 6-byte header, one 16-byte entry per image, then the data.

    Every modern Windows accepts PNG-compressed entries, which is why this
    needs no BMP encoder. Size 256 is stored as 0 by the format.
    """
    sizes = sorted(pngs)
    header = struct.pack("<HHH", 0, 1, len(sizes))
    offset = 6 + 16 * len(sizes)
    entries, blobs = b"", b""
    for s in sizes:
        data = pngs[s]
        entries += struct.pack("<BBBBHHII", s if s < 256 else 0,
                               s if s < 256 else 0, 0, 0, 1, 32,
                               len(data), offset)
        blobs += data
        offset += len(data)
    path.write_bytes(header + entries + blobs)


def main(outdir: str) -> None:
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    pngs: dict[int, bytes] = {}
    for s in sorted(set(ICNS_SIZES + ICO_SIZES)):
        data = draw(s).png()
        pngs[s] = data
        (out / f"icon_{s}.png").write_bytes(data)
        print(f"  icon_{s}.png  ({len(data)} bytes)")
    write_ico({s: pngs[s] for s in ICO_SIZES}, out / "Patchwerk.ico")
    print(f"  Patchwerk.ico")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "build/icon")

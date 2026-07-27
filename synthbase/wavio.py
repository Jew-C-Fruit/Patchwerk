"""Read and write WAV files with the standard library only.

Why not `soundfile`/`librosa`: nothing else in Patchwerk needs a compiled
audio dependency, and the two jobs here are small and fully specified. Why
not `wave`: it refuses float32, which is what scsynth writes when you ask
for headroom above 0dBFS, and it hides the sample rate conversions we care
about. So: a direct RIFF reader/writer covering exactly the formats
scsynth's ``/b_write`` can emit and ``/b_read`` can consume.

Supported: PCM 8/16/24/32-bit integer and 32/64-bit IEEE float, mono or
multichannel, plus WAVE_FORMAT_EXTENSIBLE (which scsynth emits for >2
channels). Samples are always handed back as plain Python floats
normalised to -1.0..1.0, de-interleaved into one list per channel — the
shape `tests/analysis.py` wants.

Reading a minute of stereo 48k costs ~3M floats and about a second. That
is fine for a capture window; it is not a streaming decoder and is not
trying to be.
"""

from __future__ import annotations

import struct
from pathlib import Path

WAVE_FORMAT_PCM = 0x0001
WAVE_FORMAT_IEEE_FLOAT = 0x0003
WAVE_FORMAT_EXTENSIBLE = 0xFFFE


class WavError(ValueError):
    """A file that is not a WAV we can decode, with the reason attached."""


class Wav:
    """A decoded WAV: `channels` is a list of per-channel float lists."""

    def __init__(self, channels: list[list[float]], sample_rate: int) -> None:
        self.channels = channels
        self.sample_rate = int(sample_rate)

    @property
    def channel_count(self) -> int:
        return len(self.channels)

    @property
    def frame_count(self) -> int:
        return len(self.channels[0]) if self.channels else 0

    @property
    def duration(self) -> float:
        return self.frame_count / self.sample_rate if self.sample_rate else 0.0

    def mono(self) -> list[float]:
        """Channel mean. Analysis is mono unless a check is about the image."""
        if self.channel_count == 1:
            return list(self.channels[0])
        n = self.channel_count
        return [sum(vals) / n for vals in zip(*self.channels)]

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (f"<Wav {self.channel_count}ch {self.frame_count}fr "
                f"@{self.sample_rate} ({self.duration:.3f}s)>")


# -- reading ---------------------------------------------------------------


def _chunks(data: bytes):
    """Yield (id, payload) over a RIFF body, honouring the pad byte.

    Chunks are word-aligned: an odd-sized chunk is followed by one padding
    byte that is NOT counted in its size. Ignoring that is the classic way
    to read a valid file as garbage from the second chunk onward.
    """
    pos = 0
    end = len(data)
    while pos + 8 <= end:
        cid = data[pos:pos + 4]
        (size,) = struct.unpack_from("<I", data, pos + 4)
        body = data[pos + 8:pos + 8 + size]
        yield cid, body
        pos += 8 + size + (size & 1)


def read_wav(path) -> Wav:
    raw = Path(path).read_bytes()
    if len(raw) < 12 or raw[0:4] != b"RIFF" or raw[8:12] != b"WAVE":
        raise WavError(f"{path}: not a RIFF/WAVE file")

    fmt = None
    data = None
    for cid, body in _chunks(raw[12:]):
        if cid == b"fmt " and fmt is None:
            fmt = body
        elif cid == b"data" and data is None:
            data = body
    if fmt is None or len(fmt) < 16:
        raise WavError(f"{path}: no usable 'fmt ' chunk")
    if data is None:
        raise WavError(f"{path}: no 'data' chunk")

    tag, channels, rate, _bps, _align, bits = struct.unpack_from("<HHIIHH", fmt, 0)
    if tag == WAVE_FORMAT_EXTENSIBLE:
        if len(fmt) < 40:
            raise WavError(f"{path}: truncated WAVE_FORMAT_EXTENSIBLE header")
        # The real format lives in the first two bytes of the SubFormat GUID.
        (tag,) = struct.unpack_from("<H", fmt, 24)
    if channels < 1:
        raise WavError(f"{path}: {channels} channels")

    samples = _decode(data, tag, bits, path)
    if len(samples) % channels:
        samples = samples[:len(samples) - (len(samples) % channels)]
    planes = [samples[c::channels] for c in range(channels)]
    return Wav(planes, rate)


def wav_info(path) -> dict:
    """Header only: {channels, sample_rate, bits, frames, duration}.

    Separate from `read_wav` because the injector needs the shape of a file
    to pick a synthdef and size a wait, and decoding a 30-second file into
    Python floats to learn it is two channels would be absurd.
    """
    raw = Path(path).read_bytes()
    if len(raw) < 12 or raw[0:4] != b"RIFF" or raw[8:12] != b"WAVE":
        raise WavError(f"{path}: not a RIFF/WAVE file")
    fmt = data_size = None
    for cid, body in _chunks(raw[12:]):
        if cid == b"fmt " and fmt is None:
            fmt = body
        elif cid == b"data" and data_size is None:
            data_size = len(body)
    if fmt is None or len(fmt) < 16:
        raise WavError(f"{path}: no usable 'fmt ' chunk")
    _tag, channels, rate, _bps, align, bits = struct.unpack_from("<HHIIHH", fmt, 0)
    align = align or max(1, channels * bits // 8)
    frames = (data_size or 0) // align
    return {"channels": channels, "sample_rate": rate, "bits": bits,
            "frames": frames,
            "duration": frames / rate if rate else 0.0}


def _decode(data: bytes, tag: int, bits: int, path) -> list[float]:
    """Interleaved bytes -> interleaved floats in -1..1."""
    if tag == WAVE_FORMAT_IEEE_FLOAT:
        if bits == 32:
            n = len(data) // 4
            return list(struct.unpack_from(f"<{n}f", data, 0))
        if bits == 64:
            n = len(data) // 8
            return list(struct.unpack_from(f"<{n}d", data, 0))
        raise WavError(f"{path}: {bits}-bit float is not a thing")
    if tag != WAVE_FORMAT_PCM:
        raise WavError(f"{path}: unsupported WAV format tag 0x{tag:04x}")
    if bits == 8:
        # 8-bit PCM is the odd one out: UNSIGNED, centred on 128.
        return [(b - 128) / 128.0 for b in data]
    if bits == 16:
        n = len(data) // 2
        return [v / 32768.0 for v in struct.unpack_from(f"<{n}h", data, 0)]
    if bits == 24:
        return _decode_24(data)
    if bits == 32:
        n = len(data) // 4
        return [v / 2147483648.0 for v in struct.unpack_from(f"<{n}i", data, 0)]
    raise WavError(f"{path}: unsupported bit depth {bits}")


def _decode_24(data: bytes) -> list[float]:
    """24-bit little-endian signed, the format scsynth writes by default.

    `struct` has no 3-byte code, so sign-extend by hand: the top byte's
    high bit set means subtract 2**24.
    """
    out = []
    for i in range(0, len(data) - 2, 3):
        v = data[i] | (data[i + 1] << 8) | (data[i + 2] << 16)
        if v & 0x800000:
            v -= 0x1000000
        out.append(v / 8388608.0)
    return out


# -- writing ---------------------------------------------------------------


def write_wav(path, channels: list[list[float]], sample_rate: int,
              bits: int = 24) -> Path:
    """Write float channel planes as PCM WAV. Values outside -1..1 CLIP.

    Used to synthesise known test signal for the input-injection path, so
    16/24/32-bit integer is the whole requirement; a float32 writer would
    only add a format scsynth reads more slowly.
    """
    if not channels or not channels[0]:
        raise WavError("nothing to write")
    if bits not in (16, 24, 32):
        raise WavError(f"write_wav supports 16/24/32-bit, not {bits}")
    n_ch = len(channels)
    frames = min(len(c) for c in channels)
    peak = float(1 << (bits - 1))
    limit = peak - 1

    body = bytearray()
    for i in range(frames):
        for c in channels:
            v = int(max(-limit, min(limit, round(c[i] * peak))))
            if bits == 16:
                body += struct.pack("<h", v)
            elif bits == 24:
                body += bytes(((v >> 0) & 255, (v >> 8) & 255, (v >> 16) & 255))
            else:
                body += struct.pack("<i", v)

    block = n_ch * bits // 8
    fmt = struct.pack("<HHIIHH", WAVE_FORMAT_PCM, n_ch, int(sample_rate),
                      int(sample_rate) * block, block, bits)
    if len(body) & 1:
        body += b"\x00"                      # RIFF chunks are word-aligned
    riff = (b"WAVE"
            + b"fmt " + struct.pack("<I", len(fmt)) + fmt
            + b"data" + struct.pack("<I", len(body)) + bytes(body))
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"RIFF" + struct.pack("<I", len(riff)) + riff)
    return p

#!/usr/bin/env python3
"""Prove the agent's EAR before trusting anything it says about the rig.

    python3 tests/test_analysis.py

Headless, stdlib only, no server and no audio device — so this runs in CI
where `listen.py` and `audio_proof.py` cannot.

The point is calibration. Every check here feeds `tests/analysis.py` a
signal whose features are known from arithmetic — a sine of a stated
amplitude and frequency, a square with textbook odd harmonics, an
exponential decay with a stated time constant — and asserts the extractor
recovers the number. An extractor that is 8% off (the classic un-windowed
Goertzel scallop, or a sample-rate conversion nobody applied) would still
produce confident-looking JSON, and every rig measurement built on it would
be quietly wrong in the same direction.

The WAV round-trip is here for the same reason: `capture.py` writes float32
and `inject.py` reads whatever Cole hands it, so a bit depth we decode
wrongly is a silent scaling error on everything downstream.
"""

from __future__ import annotations

import math
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import analysis as A  # noqa: E402
from synthbase import wavio  # noqa: E402

SR = 48000
PASS, FAIL = [], []


def check(name: str, ok: bool, detail: str = "") -> bool:
    (PASS if ok else FAIL).append(name)
    print(f"{'ok  ' if ok else 'FAIL'}  {name}" + (f"   [{detail}]" if detail else ""))
    return ok


def near(a: float, b: float, tol: float) -> bool:
    return abs(a - b) <= tol


def sine(freq: float, seconds: float = 1.0, amp: float = 0.5,
         sr: int = SR, phase: float = 0.0) -> list[float]:
    n = int(sr * seconds)
    w = 2.0 * math.pi * freq / sr
    return [amp * math.sin(w * i + phase) for i in range(n)]


# -- level ------------------------------------------------------------------


def test_levels() -> None:
    s = sine(1000.0, 0.5, amp=0.5)
    check("rms of a 0.5 sine is 0.5/sqrt(2)",
          near(A.rms(s), 0.5 / math.sqrt(2), 0.002), f"{A.rms(s):.5f}")
    check("peak of a 0.5 sine is 0.5", near(A.peak(s), 0.5, 0.001))
    check("crest factor of a sine is sqrt(2)",
          near(A.crest_factor(s), math.sqrt(2), 0.01),
          f"{A.crest_factor(s):.4f}")
    check("a sine has no DC offset", near(A.dc_offset(s), 0.0, 1e-3))
    check("silence reads as silent", A.is_silent([0.0] * 1000))
    check("a sine does not read as silent", not A.is_silent(s))

    dc = [x + 0.25 for x in s]
    check("dc_offset finds an offset that IS there",
          near(A.dc_offset(dc), 0.25, 1e-3), f"{A.dc_offset(dc):.5f}")
    check("db(1.0) is 0", near(A.db(1.0), 0.0, 1e-9))
    check("db(0.5) is -6.02", near(A.db(0.5), -6.0206, 0.001))


def test_zero_crossings() -> None:
    # A sine crosses zero twice per cycle.
    s = sine(440.0, 1.0)
    check("zcr of a 440 Hz sine is ~880/s",
          near(A.zero_crossing_rate(s, SR), 880.0, 4.0),
          f"{A.zero_crossing_rate(s, SR):.1f}")


# -- frequency --------------------------------------------------------------


def test_goertzel_amplitude() -> None:
    s = sine(440.0, 1.0, amp=0.5)
    got = A.goertzel(s, SR, 440.0)
    check("goertzel recovers the AMPLITUDE of a sine (0.5)",
          near(got, 0.5, 0.01), f"{got:.5f}")
    off = A.goertzel(s, SR, 1000.0)
    check("goertzel reads ~0 where there is no energy", off < 0.01,
          f"{off:.6f}")


def test_goertzel_off_bin() -> None:
    """The windowing that stops a between-bins tone reading low.

    An un-windowed single-bin DFT scallops by up to 36% when the tone does
    not land on an exact bin centre. 437.3 Hz over a 1 s window at 48 kHz
    is deliberately not a whole number of periods.
    """
    s = sine(437.3, 1.0, amp=0.5)
    got = A.goertzel(s, SR, 437.3)
    check("goertzel is accurate for a tone BETWEEN bins",
          near(got, 0.5, 0.02), f"{got:.5f} (un-windowed would scallop)")


def test_short_and_degenerate() -> None:
    check("goertzel of an empty buffer is 0", A.goertzel([], SR, 440.0) == 0.0)
    check("goertzel above nyquist is 0", A.goertzel(sine(440.0, 0.2), SR,
                                                    SR) == 0.0)
    check("rms of nothing is 0", A.rms([]) == 0.0)
    check("peak of nothing is 0", A.peak([]) == 0.0)
    check("crest of silence is 0", A.crest_factor([0.0] * 100) == 0.0)


def test_strongest_and_spectrum() -> None:
    s = [a + b for a, b in zip(sine(220.0, 1.0, 0.1), sine(660.0, 1.0, 0.4))]
    f, amp = A.strongest(s, SR, [220.0, 440.0, 660.0])
    check("strongest() picks the loudest candidate", f == 660.0 and amp > 0.3,
          f"{f} @ {amp:.4f}")
    spec = A.spectrum_at(s, SR, [220.0, 660.0])
    check("spectrum_at reports both partials",
          near(spec[220.0], 0.1, 0.01) and near(spec[660.0], 0.4, 0.02),
          f"{spec[220.0]:.4f}, {spec[660.0]:.4f}")


def test_thd() -> None:
    pure = sine(500.0, 1.0, amp=0.5)
    check("THD of a pure sine is ~0", A.thd(pure, SR, 500.0) < 0.01,
          f"{A.thd(pure, SR, 500.0):.6f}")

    # A textbook square wave: odd harmonics at 1/n of the fundamental.
    # Over harmonics 2..8 only 3, 5 and 7 are present, so THD is
    # sqrt(1/9 + 1/25 + 1/49) = 0.4141.
    n = int(SR)
    sq = [0.0] * n
    for h in (1, 3, 5, 7):
        for i, v in enumerate(sine(500.0 * h, 1.0, amp=0.5 / h)):
            sq[i] += v
    expect = math.sqrt((1 / 3) ** 2 + (1 / 5) ** 2 + (1 / 7) ** 2)
    got = A.thd(sq, SR, 500.0, harmonics=8)
    check("THD of a 4-partial square matches the 1/n law",
          near(got, expect, 0.01), f"{got:.4f} vs {expect:.4f}")


def test_centroid() -> None:
    low = sine(200.0, 0.5, amp=0.5)
    high = sine(5000.0, 0.5, amp=0.5)
    c_low, c_high = (A.spectral_centroid(low, SR),
                     A.spectral_centroid(high, SR))
    check("spectral centroid tracks a single tone",
          near(c_low, 200.0, 60.0) and near(c_high, 5000.0, 120.0),
          f"{c_low:.0f} Hz / {c_high:.0f} Hz")
    check("a brighter signal has a higher centroid", c_high > c_low * 10)


def test_alias_floor() -> None:
    clean = sine(1000.0, 1.0, amp=0.5)
    dirty = [a + b for a, b in zip(clean, sine(1337.0, 1.0, amp=0.05))]
    a_clean = A.alias_floor(clean, SR, 1000.0)
    a_dirty = A.alias_floor(dirty, SR, 1000.0)
    check("alias floor of a pure tone is low", A.db(a_clean) < -40,
          f"{A.db(a_clean):.1f} dB")
    check("a non-harmonic partial RAISES the alias floor",
          a_dirty > a_clean * 5, f"{A.db(a_dirty):.1f} dB")


# -- time -------------------------------------------------------------------


def test_envelope_and_decay() -> None:
    """An exponential decay with a known time constant.

    -60 dB is a factor of 1000, so a decay of exp(-t/tau) reaches it at
    t = tau * ln(1000) = 6.908 * tau. With tau = 0.1 that is 0.691 s.
    """
    tau, secs = 0.1, 2.0
    n = int(SR * secs)
    carrier = sine(440.0, secs, amp=1.0)
    s = [carrier[i] * math.exp(-(i / SR) / tau) for i in range(n)]
    env = A.rms_envelope(s, SR)
    check("rms_envelope covers the whole buffer",
          near(len(env) * 0.01, secs, 0.02), f"{len(env)} windows")
    check("rms_envelope decreases through a decay", env[5][1] > env[50][1])
    dt = A.decay_time(s, SR)
    check("decay_time finds the -60 dB point of a known exponential",
          dt is not None and near(dt, tau * math.log(1000), 0.03),
          f"{dt} vs {tau * math.log(1000):.3f}")

    check("decay_time is None when the tail never decays",
          A.decay_time(sine(440.0, 1.0), SR) is None)


def test_onset() -> None:
    s = [0.0] * (SR // 10) + sine(440.0, 0.5, amp=0.5)
    t = A.onset_time(s, SR, threshold=0.02)
    check("onset_time finds a 100 ms delayed start",
          t is not None and near(t, 0.1, 0.005), f"{t}")
    check("onset_time is None in silence",
          A.onset_time([0.0] * 1000, SR) is None)


def test_clicks() -> None:
    clean = sine(440.0, 1.0, amp=0.5)
    check("a clean sine has no clicks", A.clicks(clean, SR) == [],
          f"{A.clicks(clean, SR)[:3]}")

    step = A.expected_step(0.5, 440.0, SR)
    check("expected_step matches the real max step of that sine",
          near(A.max_step(clean), step, step * 0.05),
          f"{A.max_step(clean):.6f} vs {step:.6f}")

    glitched = list(clean)
    glitched[SR // 2] += 0.6                 # a one-sample discontinuity
    found = A.clicks(glitched, SR)
    check("a one-sample discontinuity IS reported",
          any(near(t, 0.5, 0.002) for t in found), f"{found[:3]}")


# -- the summary ------------------------------------------------------------


def test_analyze() -> None:
    s = sine(440.0, 1.0, amp=0.5)
    d = A.analyze(s, SR, fundamental=440.0)
    check("analyze() reports the fundamental's amplitude",
          near(d["f0_amplitude"], 0.5, 0.01), f"{d['f0_amplitude']}")
    check("analyze() carries duration and frame count",
          d["frames"] == SR and near(d["duration"], 1.0, 1e-6))
    check("analyze() omits harmonics when no fundamental is given",
          "thd" not in A.analyze(s, SR))
    check("analyze() is JSON-able", _jsonable(d))
    check("note_hz(69) is 440", near(A.note_hz(69), 440.0, 1e-9))
    check("note_hz(60) is middle C (261.63)",
          near(A.note_hz(60), 261.6256, 0.001))


def _jsonable(d) -> bool:
    import json
    try:
        json.dumps(d)
        return True
    except TypeError:
        return False


# -- wavio ------------------------------------------------------------------


def test_wav_roundtrip() -> None:
    s = sine(440.0, 0.25, amp=0.5)
    with tempfile.TemporaryDirectory() as tmp:
        for bits, tol in ((16, 1e-4), (24, 1e-6), (32, 1e-8)):
            p = Path(tmp) / f"t{bits}.wav"
            wavio.write_wav(p, [s], SR, bits=bits)
            w = wavio.read_wav(p)
            worst = max(abs(a - b) for a, b in zip(s, w.channels[0]))
            check(f"{bits}-bit WAV round-trips within its quantisation",
                  worst < tol and w.sample_rate == SR
                  and w.frame_count == len(s), f"worst {worst:.2e}")

        # Stereo, and the header-only reader agreeing with the full one.
        p = Path(tmp) / "st.wav"
        right = [-x for x in s]
        wavio.write_wav(p, [s, right], SR, bits=24)
        w = wavio.read_wav(p)
        info = wavio.wav_info(p)
        check("stereo de-interleaves to the right channels",
              w.channel_count == 2
              and max(abs(a + b) for a, b in zip(w.channels[0],
                                                 w.channels[1])) < 1e-6)
        check("wav_info agrees with a full read, without decoding",
              info["channels"] == 2 and info["frames"] == w.frame_count
              and info["sample_rate"] == SR, str(info))
        check("mono() of an anti-phase stereo pair is silence",
              A.is_silent(w.mono()))

        bad = Path(tmp) / "bad.wav"
        bad.write_bytes(b"not a wav at all")
        try:
            wavio.read_wav(bad)
            check("a non-WAV raises WavError", False)
        except wavio.WavError:
            check("a non-WAV raises WavError", True)


def test_wav_clipping() -> None:
    """Out-of-range input clips instead of wrapping.

    Integer overflow here would turn a hot signal into a full-scale
    sawtooth, i.e. "this module is loud" into "this module is broken".
    """
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "hot.wav"
        wavio.write_wav(p, [[0.0, 2.0, -2.0, 0.5]], SR, bits=16)
        got = wavio.read_wav(p).channels[0]
        check("write_wav clips rather than wrapping",
              got[1] > 0.99 and got[2] < -0.99 and near(got[3], 0.5, 1e-4),
              f"{[round(v, 4) for v in got]}")


def main() -> int:
    for fn in (test_levels, test_zero_crossings, test_goertzel_amplitude,
               test_goertzel_off_bin, test_short_and_degenerate,
               test_strongest_and_spectrum, test_thd, test_centroid,
               test_alias_floor, test_envelope_and_decay, test_onset,
               test_clicks, test_analyze, test_wav_roundtrip,
               test_wav_clipping):
        print(f"\n-- {fn.__name__} --")
        fn()
    print(f"\n{'PASS' if not FAIL else 'FAIL'} — {len(PASS)} ok, "
          f"{len(FAIL)} failed" + (f": {', '.join(FAIL)}" if FAIL else ""))
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Turn captured audio into NUMBERS. Pure standard library, no numpy.

This is the agent's ear. The build plan is explicit about why it is numbers
and not a picture: *"is there energy at 440 Hz"* is one Goertzel bin and an
exact float, where a spectrogram PNG makes the reader re-estimate that same
number off pixels. Everything here is diffable, assertable with a tolerance,
and cheap enough to run per-check.

Two entry points, and the difference matters:

* the individual measurements (`rms`, `tone_at`, `thd`, `decay_time`, ...)
  — use these when a check knows what it is asking.
* `analyze(samples, sr)` — a summary dict for when it does not: an agent
  that just captured something and wants to know what it got.

Everything takes a flat list of floats (one channel; `Wav.mono()` gives you
one) and returns plain floats, so results serialise straight to JSON and
survive a transcript.

Precision note: `goertzel` is exact for the bin it is asked about and does
not need the FFT's power-of-two framing, so frequency questions with a known
answer — a note's fundamental, a test tone, a harmonic — should go through
it. The FFT here exists for the questions that have no known answer
(centroid, "where is the energy"), and it windows and frames the signal, so
its magnitudes are estimates.
"""

from __future__ import annotations

import cmath
import math

#: Below this RMS a buffer is silence for our purposes: it is ~-80 dBFS,
#: comfortably under the noise floor of anything scsynth actually renders
#: and well above a denormal.
SILENCE_RMS = 1e-4


# -- level ------------------------------------------------------------------


def rms(samples) -> float:
    if not samples:
        return 0.0
    return math.sqrt(sum(s * s for s in samples) / len(samples))


def peak(samples) -> float:
    return max((abs(s) for s in samples), default=0.0)


def dc_offset(samples) -> float:
    """Mean sample value. A module that should be AC-coupled reads ~0 here."""
    return sum(samples) / len(samples) if samples else 0.0


def crest_factor(samples) -> float:
    """peak / rms. ~1.41 for a sine, ~1.0 for a square, high for a transient."""
    r = rms(samples)
    return peak(samples) / r if r > 0 else 0.0


def db(x: float, floor: float = -200.0) -> float:
    return 20.0 * math.log10(x) if x > 0 else floor


def is_silent(samples, threshold: float = SILENCE_RMS) -> bool:
    return rms(samples) < threshold


def zero_crossing_rate(samples, sample_rate: float) -> float:
    """Sign changes per second — a cheap brightness/noisiness proxy."""
    if len(samples) < 2:
        return 0.0
    n = sum(1 for a, b in zip(samples, samples[1:])
            if (a >= 0) != (b >= 0))
    return n * sample_rate / len(samples)


# -- frequency: Goertzel, the exact answer to an exact question --------------


def goertzel(samples, sample_rate: float, freq: float) -> float:
    """Amplitude of `freq` in `samples`, in the same units as the samples.

    A single-bin DFT. A full-scale sine at exactly `freq` reads ~1.0. The
    signal is Hann-windowed first (and the result compensated by the
    window's coherent gain of 0.5) so that a frequency landing between the
    implicit bins does not scallop away up to 36% of its amplitude — the
    single biggest source of "the tone is there but reads low".
    """
    n = len(samples)
    if n < 2 or freq <= 0 or freq >= sample_rate / 2:
        return 0.0
    k = 2.0 * math.cos(2.0 * math.pi * freq / sample_rate)
    s1 = s2 = 0.0
    for i, x in enumerate(samples):
        w = 0.5 - 0.5 * math.cos(2.0 * math.pi * i / (n - 1))
        s0 = x * w + k * s1 - s2
        s2, s1 = s1, s0
    # |X(f)| from the final two states, without the last multiply-add.
    mag = math.sqrt(s1 * s1 + s2 * s2 - k * s1 * s2)
    return 2.0 * mag / (n * 0.5)


def tone_at(samples, sample_rate: float, freq: float) -> float:
    """Alias for `goertzel` — reads better in an assertion."""
    return goertzel(samples, sample_rate, freq)


def spectrum_at(samples, sample_rate: float, freqs) -> dict[float, float]:
    """Amplitude at each of `freqs`. The shape most checks actually want."""
    return {float(f): goertzel(samples, sample_rate, f) for f in freqs}


def strongest(samples, sample_rate: float, freqs) -> tuple[float, float]:
    """(freq, amplitude) of whichever candidate holds the most energy."""
    best_f, best_a = 0.0, 0.0
    for f in freqs:
        a = goertzel(samples, sample_rate, f)
        if a > best_a:
            best_f, best_a = float(f), a
    return best_f, best_a


def thd(samples, sample_rate: float, fundamental: float,
        harmonics: int = 8) -> float:
    """Total harmonic distortion as a RATIO (not percent, not dB).

    sqrt(sum of harmonic power 2..N) / fundamental amplitude. Harmonics
    above Nyquist are skipped rather than folded — a harmonic that would
    alias is a DIFFERENT measurement (`alias_floor`) and mixing the two
    makes a clean oscillator look distorted at high fundamentals.
    """
    f0 = goertzel(samples, sample_rate, fundamental)
    if f0 <= 0:
        return 0.0
    nyq = sample_rate / 2.0
    power = 0.0
    for h in range(2, harmonics + 1):
        f = fundamental * h
        if f >= nyq:
            break
        a = goertzel(samples, sample_rate, f)
        power += a * a
    return math.sqrt(power) / f0


def alias_floor(samples, sample_rate: float, fundamental: float,
                harmonics: int = 24, tolerance: float = 0.02) -> float:
    """Loudest NON-harmonic partial, relative to the fundamental.

    Aliasing shows up as energy at frequencies that are not integer
    multiples of f0. We sweep the FFT peaks and reject any within
    `tolerance` (fractional) of a harmonic; whatever is left is the alias
    floor. Returns a ratio, so `db(alias_floor(...))` is the usual reading.
    """
    f0 = goertzel(samples, sample_rate, fundamental)
    if f0 <= 0 or fundamental <= 0:
        return 0.0
    worst = 0.0
    for f, a in _peaks(samples, sample_rate):
        h = f / fundamental
        if abs(h - round(h)) < tolerance or round(h) < 1:
            continue
        worst = max(worst, a)
    return worst / f0


def spectral_centroid(samples, sample_rate: float) -> float:
    """Amplitude-weighted mean frequency, in Hz. The "brightness" number."""
    mags, bin_hz = _magnitudes(samples, sample_rate)
    total = sum(mags)
    if total <= 0:
        return 0.0
    return sum(m * i * bin_hz for i, m in enumerate(mags)) / total


# -- time: envelopes, tails, clicks ------------------------------------------


def rms_envelope(samples, sample_rate: float,
                 hop: float = 0.01) -> list[tuple[float, float]]:
    """[(seconds, rms), ...] over non-overlapping windows of `hop`."""
    step = max(1, int(sample_rate * hop))
    return [(i / sample_rate, rms(samples[i:i + step]))
            for i in range(0, len(samples), step)]


def decay_time(samples, sample_rate: float, drop_db: float = -60.0) -> float | None:
    """Seconds from the envelope's peak until it stays `drop_db` below it.

    This is the "did the tail decay honestly, or was it cut" number. None
    means it never got that quiet inside the capture — which is itself the
    answer when a release is supposed to complete and does not.
    """
    env = rms_envelope(samples, sample_rate)
    if not env:
        return None
    pk_i, (_, pk) = max(enumerate(env), key=lambda kv: kv[1][1])
    if pk <= 0:
        return None
    target = pk * (10.0 ** (drop_db / 20.0))
    for t, v in env[pk_i:]:
        if v < target:
            return round(t - env[pk_i][0], 6)
    return None


def onset_time(samples, sample_rate: float,
               threshold: float = 0.02) -> float | None:
    """Seconds to the first sample whose magnitude crosses `threshold`."""
    for i, s in enumerate(samples):
        if abs(s) >= threshold:
            return i / sample_rate
    return None


def max_step(samples) -> float:
    """Largest sample-to-sample jump. The click detector.

    A click is a discontinuity, and a discontinuity is a first difference
    far larger than the signal's own slew. Compare against
    `expected_step(peak, freq, sr)` rather than a bare constant: a loud
    high note legitimately steps further than a quiet low one.
    """
    return max((abs(b - a) for a, b in zip(samples, samples[1:])), default=0.0)


def expected_step(amplitude: float, freq: float, sample_rate: float) -> float:
    """The largest step a clean sine of this amplitude/frequency can make."""
    return abs(amplitude) * 2.0 * math.pi * freq / sample_rate


def clicks(samples, sample_rate: float, factor: float = 8.0) -> list[float]:
    """Times (s) of steps `factor`x larger than the signal's median step.

    Median, not mean: a handful of real clicks would drag a mean up until
    they stopped looking exceptional, which is exactly backwards.
    """
    steps = [abs(b - a) for a, b in zip(samples, samples[1:])]
    if len(steps) < 16:
        return []
    ordered = sorted(steps)
    med = ordered[len(ordered) // 2]
    if med <= 0:
        med = (sum(steps) / len(steps)) or 1e-12
    limit = med * factor
    out, last = [], -1.0
    for i, s in enumerate(steps):
        t = i / sample_rate
        if s > limit and t - last > 0.005:     # one report per event
            out.append(round(t, 6))
            last = t
    return out


# -- the summary ------------------------------------------------------------


def analyze(samples, sample_rate: float, fundamental: float | None = None,
            harmonics: int = 8) -> dict:
    """Everything cheap, as one JSON-able dict.

    Pass `fundamental` when you know what note was played — the harmonic
    measurements are meaningless without it and are omitted when it is
    absent rather than guessed at.
    """
    out = {
        "frames": len(samples),
        "sample_rate": float(sample_rate),
        "duration": round(len(samples) / sample_rate, 6) if sample_rate else 0.0,
        "rms": round(rms(samples), 8),
        "rms_db": round(db(rms(samples)), 3),
        "peak": round(peak(samples), 8),
        "peak_db": round(db(peak(samples)), 3),
        "crest": round(crest_factor(samples), 4),
        "dc_offset": round(dc_offset(samples), 8),
        "silent": is_silent(samples),
        "zcr": round(zero_crossing_rate(samples, sample_rate), 2),
        "centroid": round(spectral_centroid(samples, sample_rate), 2),
        "onset": onset_time(samples, sample_rate),
        "decay_60db": decay_time(samples, sample_rate),
        "clicks": clicks(samples, sample_rate)[:12],
    }
    if fundamental:
        out["fundamental"] = float(fundamental)
        out["f0_amplitude"] = round(goertzel(samples, sample_rate, fundamental), 8)
        out["thd"] = round(thd(samples, sample_rate, fundamental, harmonics), 6)
        out["alias_floor_db"] = round(
            db(alias_floor(samples, sample_rate, fundamental)), 2)
        out["harmonics"] = {
            str(h): round(goertzel(samples, sample_rate, fundamental * h), 8)
            for h in range(1, harmonics + 1)
            if fundamental * h < sample_rate / 2
        }
    return out


def note_hz(midi_note: int, reference: float = 440.0) -> float:
    """MIDI note -> Hz, so a check can say `tone_at(..., note_hz(60))`."""
    return reference * (2.0 ** ((midi_note - 69) / 12.0))


# -- the FFT behind centroid/alias_floor -------------------------------------


#: Frame size for the estimating measurements. 4096 at 48k is ~11.7 Hz per
#: bin and ~85 ms per frame — fine enough to separate low harmonics, short
#: enough that a 1 s capture still averages a dozen frames.
FFT_N = 4096

#: Cap on frames per measurement. A pure-Python FFT is ~N*log2(N) complex
#: ops, so an unbounded 30 s capture would take minutes; 48 frames covers
#: ~4 s of audio spread across the whole capture and costs ~0.5 s.
MAX_FRAMES = 48


def _magnitudes(samples, sample_rate: float) -> tuple[list[float], float]:
    """Averaged Hann-windowed magnitude spectrum, and the Hz per bin."""
    n = FFT_N
    bin_hz = sample_rate / n
    if len(samples) < n:
        n = 1 << max(4, (len(samples).bit_length() - 1))
        if n < 16:
            return [], bin_hz
        bin_hz = sample_rate / n
    window = [0.5 - 0.5 * math.cos(2.0 * math.pi * i / (n - 1)) for i in range(n)]
    starts = list(range(0, len(samples) - n + 1, n))
    if len(starts) > MAX_FRAMES:                  # spread, do not truncate
        stride = len(starts) / MAX_FRAMES
        starts = [starts[int(i * stride)] for i in range(MAX_FRAMES)]
    if not starts:
        return [], bin_hz
    acc = [0.0] * (n // 2)
    for s in starts:
        spec = _fft([samples[s + i] * window[i] for i in range(n)])
        for i in range(n // 2):
            acc[i] += abs(spec[i])
    return [a / len(starts) for a in acc], bin_hz


def _peaks(samples, sample_rate: float) -> list[tuple[float, float]]:
    """Local maxima of the averaged spectrum as (freq, amplitude)."""
    mags, bin_hz = _magnitudes(samples, sample_rate)
    if len(mags) < 3:
        return []
    scale = 4.0 / FFT_N                 # Hann coherent gain, back to amplitude
    return [(i * bin_hz, mags[i] * scale)
            for i in range(1, len(mags) - 1)
            if mags[i] > mags[i - 1] and mags[i] >= mags[i + 1]]


def _fft(xs: list[float]) -> list[complex]:
    """Iterative radix-2 FFT. `len(xs)` must be a power of two."""
    n = len(xs)
    out = [complex(x) for x in xs]
    # bit-reversal permutation
    j = 0
    for i in range(1, n):
        bit = n >> 1
        while j & bit:
            j ^= bit
            bit >>= 1
        j |= bit
        if i < j:
            out[i], out[j] = out[j], out[i]
    size = 2
    while size <= n:
        step = cmath.exp(-2j * math.pi / size)
        half = size // 2
        for start in range(0, n, size):
            w = 1 + 0j
            for k in range(half):
                a = out[start + k]
                b = out[start + k + half] * w
                out[start + k] = a + b
                out[start + k + half] = a - b
                w *= step
        size <<= 1
    return out

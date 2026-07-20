"""Guards the power-sine coefficient law so a refactor can't silently break it.

Pure Python (no server, no numpy). Runs the same recurrence the additive module
uses and checks its two analytic limits:
    p = 2      -> pure sine (only the fundamental)
    p -> inf   -> the 4/(pi*n) square series (relative coeffs 1, 1/3, 1/5, ...)

    python tests/test_power_sine.py
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from synthbase.harmonics import power_law_coeffs  # noqa: E402


def _coeffs(p, k=8):
    # power_law_coeffs yields (n, coef); with a float `a` the coefs are plain floats
    return [c for _, c in list(power_law_coeffs(2.0 / p, partials=k))]


def test_p2_is_pure_sine():
    c = _coeffs(2.0)
    assert abs(c[0] - 1.0) < 1e-12
    assert all(abs(x) < 1e-12 for x in c[1:]), "p=2 must be a bare fundamental"


def test_square_limit_matches_4_over_pi_n():
    c = _coeffs(1e6)  # a -> 0  => square
    for i, n in enumerate(range(1, 2 * len(c), 2)):
        assert abs(c[i] - 1.0 / n) < 1e-3, f"harmonic {n} should be ~1/{n}"


if __name__ == "__main__":
    test_p2_is_pure_sine()
    test_square_limit_matches_4_over_pi_n()
    print("power-sine coefficient law OK (p=2 sine, p->inf square)")

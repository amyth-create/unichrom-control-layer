import numpy as np

from tests.synth import gaussian_chrom
from wingpc_agent.chromatogram.analysis import describe, downsample, find_peaks


def test_find_three_peaks_with_drift_and_noise():
    x, y = gaussian_chrom()
    peaks, params = find_peaks(x, y)
    rts = sorted(p.x for p in peaks)
    assert len(peaks) == 3, [p.to_dict() for p in peaks]
    for want, got in zip((9.0, 12.5, 18.0), rts):
        assert abs(want - got) < 0.1
    # heights are above baseline, so close to the gaussian amplitudes
    by_x = {round(p.x, 1): p for p in peaks}
    assert 85 < by_x[9.0].height < 105
    assert params["n_points"] == 2000


def test_no_peaks_on_flat_noise():
    rng = np.random.default_rng(1)
    x = np.linspace(0, 10, 500)
    y = 2 + rng.normal(0, 0.2, 500)
    peaks, _ = find_peaks(x, y)
    assert len(peaks) <= 1


def test_downsample_preserves_extrema():
    x, y = gaussian_chrom()
    xs, ys = downsample(x, y, 200)
    assert len(xs) <= 200
    assert abs(max(ys) - y.max()) < 1e-9
    assert abs(min(ys) - y.min()) < 1e-9


def test_describe_fields():
    x, y = gaussian_chrom()
    d = describe(x, y)
    assert d["n"] == 2000 and "noise_sigma" in d and d["x_last"] == 30.0

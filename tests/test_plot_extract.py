import numpy as np

from tests.synth import gaussian_chrom, render_plot
from wingpc_agent.chromatogram.analysis import find_peaks
from wingpc_agent.chromatogram.plot_extract import AxisCalibration, extract_trace, find_plot_area


def test_plot_area_detected_from_axes():
    x, y = gaussian_chrom()
    img, (x0, x1, y0, y1), (l, t, r, b) = render_plot(x, y)
    area = find_plot_area(np.asarray(img))
    # inner box is just inside the drawn axes
    assert abs(area.left - (l + 1)) <= 3
    assert abs(area.bottom - (b - 1)) <= 3
    assert area.width > 700 and area.height > 350


def test_extract_calibrated_trace_recovers_peaks():
    x, y = gaussian_chrom()
    img, (x0, x1, y0, y1), _ = render_plot(x, y)
    cal = AxisCalibration(x0, x1, y0, y1, "min", "mV")
    tr = extract_trace(img, calibration=cal)
    assert tr.calibrated and tr.coverage > 0.95
    assert tr.trace_rgb == (0, 0, 255)
    peaks, _ = find_peaks(tr.x, tr.y)
    rts = sorted(p.x for p in peaks)
    assert len(peaks) == 3, [p.to_dict() for p in peaks]
    for want, got in zip((9.0, 12.5, 18.0), rts):
        assert abs(want - got) < 0.15     # ~1 pixel = 0.037 min here
    # amplitude of the main peak within ~5 %
    main = max(peaks, key=lambda p: p.height)
    assert abs(main.height - 100) < 8


def test_extract_uncalibrated_returns_fractions():
    x, y = gaussian_chrom()
    img, *_ = render_plot(x, y, trace_rgb=(200, 0, 0))
    tr = extract_trace(img)
    assert not tr.calibrated
    assert tr.units == ("frac", "frac")
    assert 0 <= tr.x.min() and tr.x.max() <= 1 and 0 <= tr.y.min() and tr.y.max() <= 1
    d = tr.to_dict(100)
    assert d["n"] > 700 and len(d["x"]) <= 100


def test_monochrome_black_trace():
    x, y = gaussian_chrom()
    img, (x0, x1, y0, y1), _ = render_plot(x, y, trace_rgb=(0, 0, 0), grid=False)
    tr = extract_trace(img, calibration=AxisCalibration(x0, x1, y0, y1))
    peaks, _ = find_peaks(tr.x, tr.y)
    assert len(peaks) >= 3

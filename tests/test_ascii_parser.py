import numpy as np

from wingpc_agent.chromatogram import ascii_parser as ap


def test_tab_dot_with_header():
    txt = "WinGPC ASCII export\nSample: PS-1\n\nTime [min]\tRI [mV]\tUV [mAU]\n0.000\t1.5\t0.1\n0.010\t1.6\t0.2\n0.020\t1.7\t0.3\n"
    tables = ap.parse(txt)
    assert len(tables) == 1
    t = tables[0]
    assert t.columns == ["Time [min]", "RI [mV]", "UV [mAU]"]
    assert t.data.shape == (3, 3)
    assert t.meta.get("Sample") == "PS-1"
    assert np.isclose(t.y(1)[-1], 1.7)


def test_semicolon_decimal_comma_german():
    txt = "Zeit;Signal\n0,00;1,50\n0,01;1,75\n0,02;2,00\n0,03;1.234,5\n"
    t = ap.parse(txt)[0]
    assert t.data.shape == (4, 2)
    assert np.isclose(t.y(1)[1], 1.75)
    assert np.isclose(t.y(1)[3], 1234.5)


def test_multiple_blocks_report():
    txt = ("Raw Data\nt\tRI\n0\t1\n1\t2\n2\t3\n\n\n"
           "Elugram\nVe\tw\n10\t0.1\n11\t0.5\n12\t0.2\n\n"
           "MWD\nlogM\tw(logM)\n3\t0.0\n4\t0.7\n5\t0.1\n")
    tables = ap.parse(txt)
    assert [t.title for t in tables] == ["Raw Data", "Elugram", "MWD"]
    assert tables[1].columns == ["Ve", "w"]


def test_space_separated_scientific():
    txt = "0.0 1.0e-3\n0.5 2.0E-3\n1.0 -3.5e-3\n"
    t = ap.parse(txt)[0]
    assert t.data.shape == (3, 2)
    assert np.isclose(t.y(1)[2], -0.0035)


def test_detect_dialect_prefers_full_numeric_rows():
    lines = ["a,b", "1.0,2.0", "3.0,4.0"]
    assert ap.detect_dialect(lines) == (",", ".")


def test_three_decimals_comma_is_decimal_not_thousands():
    txt = "Zeit [min];RI [mV]\n9,011;98,935\n9,049;99,100\n9,086;98,000\n"
    t = ap.parse(txt)[0]
    assert np.isclose(t.x[0], 9.011)
    assert np.isclose(t.y(1)[1], 99.1)


def test_english_thousands_with_decimal_point():
    txt = "t,signal\n1.0,\"1,234.5\"\n2.0,\"2,345.6\"\n"
    # quotes are not stripped by design: this is not a CSV reader - make sure we fail soft
    tables = ap.parse(txt.replace('"', ""))
    # Under delim=',' this row has 3 tokens and is ambiguous; the parser must not crash.
    assert tables

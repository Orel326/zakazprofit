from crypto_trader.quality import validate_ohlcv

HOUR = 3_600_000


def test_valid_rows_pass():
    rows = [[0, 100, 110, 90, 105, 3], [HOUR, 105, 115, 100, 112, 4]]
    assert validate_ohlcv(rows, HOUR).is_valid


def test_invalid_ohlc_range_fails():
    report = validate_ohlcv([[0, 100, 95, 90, 101, 1]], HOUR)
    assert not report.is_valid
    assert any("high" in error for error in report.errors)


def test_negative_volume_fails():
    report = validate_ohlcv([[0, 100, 110, 90, 105, -1]], HOUR)
    assert not report.is_valid
    assert any("volume" in error for error in report.errors)


def test_duplicate_timestamp_fails():
    report = validate_ohlcv([[0, 100, 110, 90, 105, 1], [0, 101, 111, 91, 106, 2]], HOUR)
    assert not report.is_valid
    assert any("duplicate" in error for error in report.errors)


def test_gap_is_warning_not_error():
    report = validate_ohlcv([[0, 100, 110, 90, 105, 1], [2 * HOUR, 105, 115, 100, 112, 2]], HOUR)
    assert report.is_valid
    assert report.warnings


def test_empty_dataset_fails():
    assert not validate_ohlcv([], HOUR).is_valid

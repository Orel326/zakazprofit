import io
import unittest
import zipfile
from datetime import date, datetime, timezone
from crypto_trader.replay_history import months, parse_archive, load, START, DEV_END, SELECTION_END, END

def archive(*rows):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("candles.csv", "\n".join(rows))
    return buf.getvalue()

class ReplayHistoryTests(unittest.TestCase):
    def test_frozen_boundaries(self):
        self.assertEqual(len(list(months(START, DEV_END))), 24)
        self.assertEqual(len(list(months(DEV_END, SELECTION_END))), 24)
        self.assertEqual(len(list(months(SELECTION_END, END))), 12)
        self.assertEqual(list(months(START, SELECTION_END))[-1], "2025-10")
        with self.assertRaises(KeyError):
            load("unused", phase="holdout")

    def test_timestamp_units(self):
        ts = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp()) * 1000
        for stamp in (ts, ts * 1000):
            self.assertEqual(parse_archive(archive(f"{stamp},10,12,9,11,5"), "2025-01"), [[ts,10,12,9,11,5]])

    def test_reject_invalid_rows(self):
        ts = 1735689600000
        row = f"{ts},10,12,9,11,5"
        for rows in ((row,row), (f"{ts},10,8,9,11,5",), (f"{ts},10,12,9,11,nan",), (f"{ts+1},10,12,9,11,5",)):
            with self.assertRaises(ValueError):
                parse_archive(archive(*rows), "2025-01")
        with self.assertRaises(ValueError):
            parse_archive(archive(row), "2024-12")

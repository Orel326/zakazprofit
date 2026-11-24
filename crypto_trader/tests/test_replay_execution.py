import unittest
from crypto_trader.replay_execution import Ledger, DAY

def candle(day, opening=100, close=100):
    return (day, opening, max(opening, close), min(opening, close), close, 1)

class ExecutionTests(unittest.TestCase):
    def test_round_trip_costs(self):
        ledger = Ledger(1000, .01, .02)
        ledger.execute("BTC", 0, candle(DAY), 2)
        self.assertAlmostEqual(ledger.cash, 793.96)
        self.assertAlmostEqual(ledger.equity(DAY, {"BTC": candle(DAY)}), 993.96)
        pnl = ledger.execute("BTC", DAY, candle(2*DAY), -2)
        self.assertAlmostEqual(pnl, -12)
        self.assertAlmostEqual(ledger.cash, 988)
        self.assertAlmostEqual(ledger.costs, 12)
        self.assertFalse(ledger.positions)

    def test_partial_sale_basis(self):
        ledger = Ledger(1000, 0, 0)
        ledger.execute("BTC", 0, candle(DAY), 2)
        self.assertEqual(ledger.execute("BTC", DAY, candle(2*DAY, 120), -1), 20)
        self.assertEqual(ledger.positions["BTC"], (1, 100))
        self.assertEqual(ledger.equity(2*DAY, {"BTC": candle(2*DAY, 120, 130)}), 1050)

    def test_invalid_fills_atomic(self):
        ledger = Ledger(1000, .01, .02)
        for row, quantity in [(None, 1), (candle(0), 1), (candle(2*DAY), 1),
                              (candle(DAY), 10), (candle(DAY), -1),
                              (candle(DAY), float("nan")), (candle(DAY, 0), 1)]:
            with self.assertRaises(ValueError):
                ledger.execute("BTC", 0, row, quantity)
            self.assertEqual(ledger.cash, 1000)
            self.assertEqual(ledger.costs, 0)
            self.assertFalse(ledger.positions)
            self.assertFalse(ledger.trades)

    def test_missing_mark(self):
        ledger = Ledger(1000, 0, 0)
        ledger.execute("BTC", 0, candle(DAY), 1)
        for marks in ({}, {"BTC": candle(0)}, {"BTC": candle(DAY, close=0)}):
            with self.assertRaises(ValueError):
                ledger.equity(DAY, marks)

    def test_next_open_not_close(self):
        ledger = Ledger(1000, 0, 0)
        ledger.execute("BTC", 0, candle(DAY, 120, 150), 1)
        self.assertEqual(ledger.cash, 880)
        self.assertEqual(ledger.trades[0]["fill_day"], DAY)
        self.assertEqual(ledger.equity(DAY, {"BTC": candle(DAY, 120, 150)}), 1030)

if __name__ == "__main__":
    unittest.main()

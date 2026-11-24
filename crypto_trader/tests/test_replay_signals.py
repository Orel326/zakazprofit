import unittest
from datetime import datetime, timezone
from crypto_trader.replay_signals import DAY, signals, relative_targets, window

class ReplaySignalsTests(unittest.TestCase):
    def setUp(self):
        self.day = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp()*1000)
        self.rows = {}
        for i in range(220):
            t = self.day - (219-i)*DAY
            p = 100+i
            self.rows[t] = (t,p,p+1,p-1,p,100)

    def test_prefix_invariance(self):
        expected = signals(self.rows, self.rows, self.day)
        future = dict(self.rows)
        for i in range(1, 20):
            t = self.day+i*DAY
            future[t] = (t,1,100000,1,99999,1000000)
        self.assertEqual(expected, signals(future, future, self.day))
        self.assertTrue(expected["regime"])
        self.assertGreater(expected["relative_strength"], 0)

    def test_gap_disables_lookback(self):
        rows = dict(self.rows)
        del rows[self.day-DAY]
        self.assertIsNone(window(rows,self.day,2))
        self.assertTrue(all(v is None for v in signals(rows,self.rows,self.day).values()))

    def test_btc_gap_disables_regime_and_shock(self):
        btc = dict(self.rows)
        del btc[self.day-100*DAY]
        result = signals(self.rows,btc,self.day)
        self.assertIsNone(result["regime"])
        self.assertIsNone(result["shock"])

    def test_shock(self):
        rows = dict(self.rows)
        t = self.day
        rows[t] = (t,318,319,280,280,100)
        self.assertTrue(signals(rows,self.rows,t)["shock"])

    def test_breakout(self):
        rows = dict(self.rows)
        t = self.day
        rows[t] = (t,319,331,318,330,300)
        self.assertTrue(signals(rows,self.rows,t)["volume_breakout"])
        rows[t] = (t,319,331,318,330,200)
        self.assertFalse(signals(rows,self.rows,t)["volume_breakout"])

    def test_rank_ties_and_schedule(self):
        scores = {"B": .2, "A": .2, "C": .1, "D": None, "E": 0, "F": -.1}
        self.assertEqual(relative_targets(scores,self.day,2), ("A","B"))
        self.assertIsNone(relative_targets(scores,self.day+DAY,2))

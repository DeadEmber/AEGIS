import tempfile, unittest, datetime as dt
from pathlib import Path
from aegis import Engine, Config, Snapshot, demo, UTC

class Tests(unittest.TestCase):
    def test_demo_and_cash(self):
        with tempfile.TemporaryDirectory() as d:
            e=Engine(str(Path(d)/'db.sqlite'),Config()); t=demo(e); s=e.status(t)
            self.assertEqual(s['buys'],2); self.assertEqual(s['sales'],1); self.assertEqual(s['contributed_eur'],100)
            self.assertAlmostEqual(s['subtotal_excluding_stale_eur'],110.41,places=2)
            e.cycle(t,[]); self.assertEqual(e.cash()['contributed'],100)
            self.assertEqual(e.status(t+dt.timedelta(hours=1))['stale_positions_unvalued'],1)
            self.assertEqual(e.status(t+dt.timedelta(hours=1))['open_positions'],1)
    def test_new_month_and_no_duplicate_mint(self):
        with tempfile.TemporaryDirectory() as d:
            e=Engine(str(Path(d)/'db.sqlite'),Config()); t=dt.datetime(2026,1,1,tzinfo=UTC)
            s=Snapshot('m','p','X',.00001,20000,100000,0,10000,50,20,.1,'2025-12-25T00:00:00+00:00')
            e.cycle(t,[s,s]); e.cycle(t,[s]); self.assertEqual(e.status(t)['buys'],1)
            e.cycle(t+dt.timedelta(days=35),[]); self.assertEqual(e.cash()['contributed'],200)
if __name__=='__main__': unittest.main()

"""AEGIS paper-only research prototype. No wallet or real orders."""
import argparse, csv, datetime as dt, json, math, os, sqlite3, time, urllib.request
from dataclasses import dataclass, asdict
from pathlib import Path

UTC=dt.timezone.utc
BASE='https://api.geckoterminal.com/api/v2'
KNOWN={'So11111111111111111111111111111111111111112','EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v','Es9vMFrzaCERmJfrF4H2FYD8Vw4Cny2cFJ4f3R3R7G9'}

def now(): return dt.datetime.now(UTC)
def stamp(t): return t.isoformat()
def num(x):
    try:
        v=float(x); return v if math.isfinite(v) else 0.0
    except (TypeError,ValueError): return 0.0

def pct(a,b): return a/b-1 if b>0 else 0

@dataclass
class Snapshot:
    mint:str; pool:str; name:str; price:float; liquidity:float; fdv:float; cap:float; volume:float; buys:int; sells:int; change:float; created:str=''

@dataclass
class Config:
    eurusd:float=1.10 # demo only; live requires explicit rate
    contribution:float=100.0
    ticket:float=1.0 # all-in entry budget incl fixed fee
    fixed_fee:float=0.03
    swap_fee:float=0.005
    min_liquidity:float=10000.0 # USD
    min_volume:float=5000.0 # USD/24h
    min_fdv:float=25000.0
    max_fdv:float=5000000.0
    max_price_eur:float=.001
    min_age_hours:float=24.0
    max_new_per_cycle:int=10
    reserve_fraction:float=.10 # of realized gains only
    risk_partial:float=.55
    risk_exit:float=.80
    min_sale_eur:float=.10

class Feed:
    def __init__(self): self.last=0
    def get(self,path):
        time.sleep(max(0,6.2-(time.monotonic()-self.last)))
        self.last=time.monotonic()
        req=urllib.request.Request(BASE+path,headers={'Accept':'application/json','User-Agent':'AEGIS-paper-research/0.1'})
        with urllib.request.urlopen(req,timeout=25) as r: return json.load(r)['data']
    @staticmethod
    def parse(p):
        a=p['attributes']; rel=p.get('relationships',{}).get('base_token',{}).get('data',{}) or {}
        mint=rel.get('id','').removeprefix('solana_')
        t=a.get('transactions',{}).get('h1',{}) or {}
        return Snapshot(mint,a.get('address',''),a.get('name',''),num(a.get('base_token_price_usd')),num(a.get('reserve_in_usd')),num(a.get('fdv_usd')),num(a.get('market_cap_usd')),num(a.get('volume_usd',{}).get('h24')),int(num(t.get('buys'))),int(num(t.get('sells'))),num(a.get('price_change_percentage',{}).get('h1'))/100,a.get('pool_created_at') or '')
    def discover(self,pages=2):
        out=[]
        for page in range(1,pages+1):
            out.extend(self.parse(p) for p in self.get(f'/networks/solana/new_pools?page={page}'))
        return out
    def refresh(self,pools):
        out=[]
        for i in range(0,len(pools),20):
            ids=','.join(pools[i:i+20]); out.extend(self.parse(p) for p in self.get(f'/networks/solana/pools/multi/{ids}'))
        return out

class Engine:
    def __init__(self,path,cfg):
        self.c=cfg; self.db=sqlite3.connect(path); self.db.row_factory=sqlite3.Row
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT);
        CREATE TABLE IF NOT EXISTS cash(id INTEGER PRIMARY KEY CHECK(id=1),available REAL,reserve REAL,contributed REAL);
        INSERT OR IGNORE INTO cash VALUES(1,0,0,0);
        CREATE TABLE IF NOT EXISTS contributions(month TEXT PRIMARY KEY,ts TEXT,amount REAL);
        CREATE TABLE IF NOT EXISTS tokens(mint TEXT PRIMARY KEY,pool TEXT,name TEXT,first_seen TEXT,last_seen TEXT,price REAL,liquidity REAL,high REAL,low_after_high REAL,entries INTEGER DEFAULT 0,last_exit TEXT);
        CREATE TABLE IF NOT EXISTS snapshots(id INTEGER PRIMARY KEY,ts TEXT,mint TEXT,pool TEXT,price REAL,liquidity REAL,fdv REAL,cap REAL,volume REAL,buys INTEGER,sells INTEGER,change REAL);
        CREATE TABLE IF NOT EXISTS positions(mint TEXT PRIMARY KEY,qty REAL,basis REAL,entry_price REAL,peak REAL,partial INTEGER,opened TEXT);
        CREATE TABLE IF NOT EXISTS decisions(id INTEGER PRIMARY KEY,ts TEXT,mint TEXT,action TEXT,reason TEXT,risk REAL,price REAL,qty REAL,cash_delta REAL,reserve_delta REAL,realized REAL);
        '''); self.db.commit()
    def cash(self): return self.db.execute('SELECT * FROM cash WHERE id=1').fetchone()
    def fund(self,t):
        month=t.strftime('%Y-%m')
        if self.db.execute('INSERT OR IGNORE INTO contributions VALUES(?,?,?)',(month,stamp(t),self.c.contribution)).rowcount:
            self.db.execute('UPDATE cash SET available=available+?,contributed=contributed+? WHERE id=1',(self.c.contribution,self.c.contribution))
    def pools(self):
        # Bounded refresh: oldest active positions first, then oldest watchlist tokens.
        # At scale this must be replaced by streaming and adaptive scheduling.
        return [r[0] for r in self.db.execute('''SELECT t.pool FROM tokens t LEFT JOIN positions p USING(mint) WHERE t.pool != '' ORDER BY (p.mint IS NULL),t.last_seen ASC LIMIT 200''')]
    def log(self,t,s,action,reason,risk=0,qty=0,cash=0,reserve=0,pnl=0):
        self.db.execute('INSERT INTO decisions(ts,mint,action,reason,risk,price,qty,cash_delta,reserve_delta,realized) VALUES(?,?,?,?,?,?,?,?,?,?)',(stamp(t),s.mint,action,reason,risk,s.price,qty,cash,reserve,pnl))
    def eligible(self,s,t):
        c=self.c
        try: age=(t-dt.datetime.fromisoformat(s.created.replace('Z','+00:00'))).total_seconds()/3600
        except (ValueError,TypeError): return False
        return (s.price/c.eurusd<=c.max_price_eur and s.liquidity>=c.min_liquidity and s.volume>=c.min_volume and c.min_fdv<=s.fdv<=c.max_fdv and age>=c.min_age_hours and s.buys+s.sells>=10)
    def impact(self,s,notional_eur):
        # Approximation ONLY: assumes equal-value reserves and constant-product pool.
        reserve=s.liquidity/2/self.c.eurusd
        return notional_eur/(reserve+notional_eur) if reserve>0 else 1
    def proceeds(self,s,qty):
        gross=qty*s.price/self.c.eurusd
        return max(0,gross*(1-self.impact(s,gross))*(1-self.c.swap_fee)-self.c.fixed_fee)
    def buy(self,t,s,reason):
        c=self.c
        if self.cash()['available']+1e-8<c.ticket or c.ticket<=c.fixed_fee: return False
        spend=c.ticket-c.fixed_fee; qty=spend*(1-c.swap_fee)*(1-self.impact(s,spend))*c.eurusd/s.price
        self.db.execute('INSERT INTO positions VALUES(?,?,?,?,?,?,?)',(s.mint,qty,c.ticket,s.price,s.price,0,stamp(t)))
        self.db.execute('UPDATE tokens SET entries=entries+1 WHERE mint=?',(s.mint,))
        self.db.execute('UPDATE cash SET available=available-? WHERE id=1',(c.ticket,))
        self.log(t,s,'BUY',reason,qty=qty,cash=-c.ticket); return True
    def sell(self,t,s,p,fraction,risk,reason):
        qty=p['qty']*fraction; value=self.proceeds(s,qty)
        if value<self.c.min_sale_eur:
            self.log(t,s,'NO_EXIT','Estimated proceeds below minimum; not a confirmed dead token',risk); return
        basis=p['basis']*fraction; pnl=value-basis; reserve=max(0,pnl)*self.c.reserve_fraction; free=value-reserve
        self.db.execute('UPDATE cash SET available=available+?,reserve=reserve+? WHERE id=1',(free,reserve))
        if fraction>=1:
            self.db.execute('DELETE FROM positions WHERE mint=?',(s.mint,)); self.db.execute('UPDATE tokens SET last_exit=?,low_after_high=? WHERE mint=?',(stamp(t),s.price,s.mint))
        else: self.db.execute('UPDATE positions SET qty=qty-?,basis=basis-?,partial=1 WHERE mint=?',(qty,basis,s.mint))
        self.log(t,s,'SELL' if fraction>=1 else 'PARTIAL',reason,risk,qty,free,reserve,pnl)
    def cycle(self,t,snaps):
        with self.db:
            self.fund(t); opened=0
            best={}
            for s in snaps:
                if not s.mint or s.mint in KNOWN or not s.pool or s.price<=0: continue
                if s.mint not in best or s.liquidity>best[s.mint].liquidity: best[s.mint]=s
            for s in best.values():
                old=self.db.execute('SELECT * FROM tokens WHERE mint=?',(s.mint,)).fetchone()
                if old is None:
                    self.db.execute('INSERT INTO tokens VALUES(?,?,?,?,?,?,?,?,?,?,?)',(s.mint,s.pool,s.name,stamp(t),stamp(t),s.price,s.liquidity,s.price,s.price,0,None))
                else:
                    high=max(old['high'],s.price); low=s.price if s.price>=old['high'] else min(old['low_after_high'],s.price)
                    self.db.execute('UPDATE tokens SET pool=?,name=?,last_seen=?,price=?,liquidity=?,high=?,low_after_high=? WHERE mint=?',(s.pool,s.name,stamp(t),s.price,s.liquidity,high,low,s.mint))
                self.db.execute('INSERT INTO snapshots(ts,mint,pool,price,liquidity,fdv,cap,volume,buys,sells,change) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(stamp(t),s.mint,s.pool,s.price,s.liquidity,s.fdv,s.cap,s.volume,s.buys,s.sells,s.change))
                p=self.db.execute('SELECT * FROM positions WHERE mint=?',(s.mint,)).fetchone()
                # Different pool liquidity is not comparable: omit this component.
                liq_drop=max(0,-pct(s.liquidity,old['liquidity'])) if old and old['pool']==s.pool else 0
                balance=s.buys/max(1,s.buys+s.sells)
                if p:
                    peak=max(s.price,p['peak']); draw=1-s.price/peak; multiple=s.price/p['entry_price']
                    self.db.execute('UPDATE positions SET peak=? WHERE mint=?',(peak,s.mint))
                    # Transparent prototype heuristic, NOT calibrated probability.
                    risk=.35*min(1,draw/.40)+.30*min(1,liq_drop/.50)+.25*max(0,min(1,(.50-balance)/.35))+.10*max(0,min(1,-s.change/.30))
                    if s.liquidity<1000 or (risk>=self.c.risk_exit and draw>=.30): self.sell(t,s,p,1,risk,'Critical liquidity/drawdown with selling pressure')
                    elif multiple>=2 and risk>=self.c.risk_partial and not p['partial']: self.sell(t,s,p,.65,risk,'Profitable position with deterioration; preserve residual')
                    else: self.log(t,s,'HOLD','No exit condition',risk)
                elif opened<self.c.max_new_per_cycle and self.eligible(s,t):
                    if old is None or old['entries']==0:
                        if self.buy(t,s,'Broad discovery; security NOT verified (paper only)'): opened+=1
                    elif old['last_exit'] and old['entries']<2:
                        elapsed=(t-dt.datetime.fromisoformat(old['last_exit'])).total_seconds()/3600
                        if elapsed>=24 and old['low_after_high']<=old['high']*.15 and s.price>=old['low_after_high']*1.5 and balance>=.60 and s.change>0 and old['pool']==s.pool and pct(s.liquidity,old['liquidity'])>=.10:
                            if self.buy(t,s,'Experimental second-wave rule; uncalibrated'): opened+=1
                # No missing-data=zero inference: stale positions remain marked stale in status.
    def status(self,t):
        c=self.cash(); total=0; stale=0; open_count=0
        for p in self.db.execute('SELECT p.qty,t.* FROM positions p JOIN tokens t USING(mint)'):
            open_count+=1; age=(t-dt.datetime.fromisoformat(p['last_seen'])).total_seconds()/60
            if age>30: stale+=1; continue
            total+=self.proceeds(Snapshot(p['mint'],p['pool'],p['name'],p['price'],p['liquidity'],0,0,0,0,0,0),p['qty'])
        return {'as_of':stamp(t),'contributed_eur':round(c['contributed'],2),'available_eur':round(c['available'],2),'reserve_eur':round(c['reserve'],2),'fresh_position_estimate_eur':round(total,2),'subtotal_excluding_stale_eur':round(c['available']+c['reserve']+total,2),'open_positions':open_count,'stale_positions_unvalued':stale,'tracked_tokens':self.db.execute('SELECT count(*) FROM tokens').fetchone()[0],'buys':self.db.execute("SELECT count(*) FROM decisions WHERE action='BUY'").fetchone()[0],'sales':self.db.execute("SELECT count(*) FROM decisions WHERE action IN ('SELL','PARTIAL')").fetchone()[0]}
    def export(self,path):
        path=Path(path); path.mkdir(parents=True,exist_ok=True)
        for table in ('contributions','tokens','snapshots','positions','decisions','cash'):
            cur=self.db.execute('SELECT * FROM '+table)
            with (path/(table+'.csv')).open('w',newline='',encoding='utf-8') as f:
                w=csv.writer(f); w.writerow([d[0] for d in cur.description]); w.writerows(cur)

def demo(e):
    start=dt.datetime(2026,1,1,12,tzinfo=UTC)
    rows=[(0,.00001,20000,80,20,.10),(1,.00020,80000,90,10,1.0),(2,.00012,35000,20,80,-.40),(3,.00005,8000,5,95,-.60),(4,.000008,12000,20,80,-.50),(30,.00002,25000,80,20,.50)]
    for hour,price,liq,b,s,ch in rows:
        t=start+dt.timedelta(hours=hour); snap=Snapshot('demo_mint','demo_pool','DEMO',price,liq,100000,0,30000,b,s,ch,'2025-12-28T00:00:00+00:00'); e.cycle(t,[snap])
    return t

def main():
    p=argparse.ArgumentParser(); p.add_argument('mode',choices=['demo','once','run','status','export']); p.add_argument('--db',default=None); p.add_argument('--eurusd',type=float); p.add_argument('--interval',type=int,default=300); p.add_argument('--pages',type=int,default=2); p.add_argument('--out',default='exports'); args=p.parse_args()
    if args.mode in ('once','run') and (args.eurusd is None or not math.isfinite(args.eurusd) or args.eurusd<=0): p.error('Live paper mode requires explicit --eurusd (USD per EUR); update regularly.')
    db=args.db or ('demo.sqlite' if args.mode=='demo' else 'paper.sqlite')
    if args.mode=='demo' and Path(db).exists(): p.error('Use a new demo database to avoid mixing simulations.')
    if args.interval<60 or not 1<=args.pages<=5: p.error('interval >= 60 seconds and pages 1..5')
    e=Engine(db,Config(eurusd=args.eurusd or 1.10)); rate=e.db.execute("SELECT value FROM settings WHERE key='eurusd'").fetchone()
    if args.eurusd is None and rate: e.c.eurusd=float(rate[0])
    stored=e.db.execute("SELECT value FROM settings WHERE key='mode'").fetchone()
    mode='demo' if args.mode=='demo' else 'live'
    if args.mode in ('demo','once','run'):
        if stored and stored[0]!=mode: p.error('Cannot mix demo and live databases.')
        e.db.execute("INSERT OR REPLACE INTO settings VALUES('mode',?)",(mode,)); e.db.execute("INSERT OR REPLACE INTO settings VALUES('eurusd',?)",(str(e.c.eurusd),)); e.db.commit()
    if args.mode=='demo': print(json.dumps(e.status(demo(e)),indent=2)); e.export(args.out); return
    if args.mode=='export': e.export(args.out); return
    if args.mode=='status':
        t=now()
        if stored and stored[0]=='demo':
            last=e.db.execute('SELECT max(ts) FROM snapshots').fetchone()[0]
            if last: t=dt.datetime.fromisoformat(last)
        print(json.dumps(e.status(t),indent=2)); return
    feed=Feed()
    while True:
        begin=time.monotonic()
        try:
            discovered=feed.discover(args.pages); existing=feed.refresh(e.pools()); e.cycle(now(),existing+discovered); print(json.dumps(e.status(now())),flush=True)
        except Exception as exc:
            print(f'Feed/cycle error; no fabricated fills: {exc}',flush=True)
            if args.mode=='once': raise SystemExit(1)
        if args.mode=='once': break
        time.sleep(max(5,args.interval-(time.monotonic()-begin)))
if __name__=='__main__': main()

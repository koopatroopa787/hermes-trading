import os
env_path = 'config/broker.env'
with open(env_path) as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith('#'):
            k, _, v = line.partition('=')
            os.environ[k.strip()] = v.strip()
from alpaca.trading.client import TradingClient
tc = TradingClient(os.environ['ALPACA_API_KEY'], os.environ['ALPACA_SECRET_KEY'], paper=True)
acct = tc.get_account()
print(f'WQ Brain Account: ${float(acct.equity):,.2f}')
positions = tc.get_all_positions()
print(f'Open Positions: {len(positions)}')
for p in positions:
    qty = float(p.qty); entry = float(p.avg_entry_price); curr = float(p.current_price)
    pnl = float(p.unrealized_pl); side = 'SHORT' if qty < 0 else 'LONG'
    print(f'  {p.symbol}: {abs(qty)}x @ ${entry:.2f} -> ${curr:.2f} ({side}) PnL: ${pnl:+.2f}')

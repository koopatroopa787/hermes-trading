import os
env_path = 'config/broker.env'
with open(env_path) as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith('#'):
            k, _, v = line.partition('=')
            os.environ[k.strip()] = v.strip()
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce

tc = TradingClient(os.environ['ALPACA_API_KEY'], os.environ['ALPACA_SECRET_KEY'], paper=True)
positions = tc.get_all_positions()
print(f'Checking {len(positions)} positions...')

# Only close SHORTS (stale from yesterday) — leave BBAI long (active trade today)
for p in positions:
    qty = float(p.qty)
    if qty < 0:
        # Stale short position -> buy to cover
        order = MarketOrderRequest(
            symbol=p.symbol,
            qty=int(abs(qty)),
            side=OrderSide.BUY,
            time_in_force=TimeInForce.DAY,
        )
        resp = tc.submit_order(order)
        print(f'  BUY {int(abs(qty))} {p.symbol} (close short @ ${float(p.current_price):.2f}) — ID: {resp.id}')
    elif qty > 0:
        print(f'  Skipping {p.symbol} LONG {int(qty)} — active position')

print('\nDone. Check fills shortly.')

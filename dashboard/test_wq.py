import os
env_path = "/home/ubuntu/trading-bot/config/broker.env"
env_vars = {}
with open(env_path) as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith('#'):
            k, _, v = line.partition('=')
            env_vars[k.strip()] = v.strip()

from alpaca.trading.client import TradingClient
tc = TradingClient(env_vars["ALPACA_API_KEY"], env_vars["ALPACA_SECRET_KEY"], paper=True)
acct = tc.get_account()
print(f"WQ: ${float(acct.equity):,.2f} | Cash: ${float(acct.cash):,.2f}")

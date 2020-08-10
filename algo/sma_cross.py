from pylivetrader.api import order_target_percent, symbol

import logbook
import talib

log = logbook.Logger('algo')

def initialize(context):
    context.i = 0
    context.asset = symbol('SPY')

def handle_data(context, data):
    # Compute averages
    # data.history() has to be called with the same params
    # from above and returns a pandas dataframe.
    prices = data.history(context.asset, 'price', bar_count=10000, frequency="1m")
    prices = prices.resample('240T').last().dropna()
    print(prices)

    short_mavg = talib.EMA(prices, 20)
    long_mavg = talib.EMA(prices, 40)

    log.info(
            '''
            Short: %s
            Long:  %s
            ''' % (short_mavg, long_mavg))
    # Trading logic
    if short_mavg > long_mavg:
        # order_target orders as many shares as needed to
        # achieve the desired number of shares.
        order_target_percent(context.asset, 1)
    elif short_mavg < long_mavg:
        order_target_percent(context.asset, 0)

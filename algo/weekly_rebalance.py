from pylivetrader.api import (
    schedule_function,
    date_rules,
    time_rules,
    get_datetime,
    get_open_orders,
    order_target_percent,
    cancel_order,
    symbol,
)
import talib
import logbook
import requests

LOG = logbook.Logger("algo")


def record(*args, **kwargs):
    """Records variables to the log"""
    LOG.info("args={}, kwargs={}".format(args, kwargs))


def initialize(context):
    """Sets up the context"""
    context.stocks = {
        symbol("TYD"): 0.1,
        symbol("TMF"): 0.2,
        symbol("SPXL"): 0.5,
        symbol("VNQ"): 0.2,
    }

    schedule_function(
        rebalance, date_rules.week_start(), time_rules.market_open(minutes=11)
    )


def handle_data(context, data):
    first_run_complete = getattr(context, "last_ran_buy", False)
    if not first_run_complete:
        rebalance(context, data)
        context.first_run_complete = True


def rebalance(context, data):
    """Rebalance the portfolio based on context.stocks"""

    for stock, weight in context.stocks.items():
        if not get_open_orders(stock):
            prices = get_prices(stock, data)
            LOG.info("checking buy or sell on %s" % stock.symbol)
            if should_buy(prices):
                LOG.info("BUY %s: %s" % (stock.symbol, calculate(prices)))
                order_target_percent(stock, weight)
            elif should_sell(prices):
                LOG.info("SELL %s: %s" % (stock.symbol, calculate(prices)))
                order_target_percent(stock, 0)


def get_prices(stock, data):
    for i in range(5):
        try:
            return data.history(stock, "price", bar_count=200, frequency="1d")
        except requests.exceptions.HTTPError:
            if i < 3 - 1:
                continue
            else:
                raise


def should_buy(prices):
    """Calculates the mean regression for the buy signal"""
    metrics = calculate(prices)

    return metrics["sema"] > (1.001 * metrics["lema"])


def should_sell(prices):
    """Calculates the mean regression for the sell signal"""
    metrics = calculate(prices)

    return metrics["ssma"] < metrics["lsma"]


def calculate(prices):
    return {
        "sema": talib.EMA(prices, 34)[-1],
        "lema": talib.EMA(prices, 200)[-1],
        "ssma": talib.SMA(prices, 40)[-1],
        "lsma": talib.SMA(prices, 200)[-1],
    }

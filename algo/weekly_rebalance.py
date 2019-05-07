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
        rebalance, date_rules.week_start(days_offset=1), time_rules.market_open(minutes=11)
    )


def rebalance(context, data):
    """Rebalance the portfolio based on context.stocks"""

    for stock, weight in context.stocks.items():
        if data.can_trade(stock) and not get_open_orders(stock):
            prices = data.history(stock, "price", bar_count=200, frequency="1d")
            if should_buy(prices):
                order_target_percent(stock, weight)
            elif should_sell(prices):
                order_target_percent(stock, 0)


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
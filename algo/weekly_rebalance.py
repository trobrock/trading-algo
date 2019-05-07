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
        symbol("TYD"): 0.2,
        symbol("TMF"): 0.2,
        symbol("SPXL"): 0.6,
    }

    schedule_function(
        rebalance, date_rules.every_day(), time_rules.market_open(minutes=11)
    )


def rebalance(context, data):
    """Rebalance the portfolio based on context.stocks"""

    LOG.info("rebalancing")
    for stock, weight in context.stocks.items():
        if not get_open_orders(stock):
            order_target_percent(stock, weight)

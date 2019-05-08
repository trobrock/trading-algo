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
from pylivetrader.finance.execution import LimitOrder
import talib
import logbook
import requests

LOG = logbook.Logger("algo")


def record(*args, **kwargs):
    """Records variables to the log"""
    LOG.info("args={}, kwargs={}".format(args, kwargs))


def initialize(context):
    """Sets up the context"""
    context.stocks = {symbol("TYD"): 0.2, symbol("TMF"): 0.2, symbol("SPXL"): 0.6}

    context.target_leverage = 1

    schedule_function(
        rebalance, date_rules.every_day(), time_rules.market_open(minutes=11)
    )


def rebalance(context, data):
    """Rebalance the portfolio based on context.stocks"""

    LOG.info("cancelling open orders")
    for order in get_open_orders():
        cancel_order(order)

    LOG.info("rebalancing")
    for stock, weight in context.stocks.items():
        LOG.info("%s: %.2f percent" % (stock.symbol, weight * 100))
        price = data.current(stock, "price")
        limit = price + (price * 0.01)
        order_target_percent(
            stock, weight * context.target_leverage, style=LimitOrder(limit)
        )

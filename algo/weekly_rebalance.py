from pylivetrader.api import (
    schedule_function,
    date_rules,
    time_rules,
    get_datetime,
    get_open_orders,
    order,
    cancel_order,
    symbol,
)
from pylivetrader.finance.execution import LimitOrder
import talib
import logbook
import requests
from math import floor

LOG = logbook.Logger("algo")


def record(*args, **kwargs):
    """Records variables to the log"""
    LOG.info("args={}, kwargs={}".format(args, kwargs))


def initialize(context):
    """Sets up the context"""
    context.stocks = {symbol("TMF"): 0.2, symbol("UJB"): 0.2, symbol("TQQQ"): 0.6}

    context.target_leverage = 1

    schedule_function(
        rebalance, date_rules.every_day(), time_rules.market_open(minutes=11)
    )


def rebalance(context, data):
    """Rebalance the portfolio based on context.stocks"""

    LOG.info("cancelling open orders")
    cancel_all_orders(context, data)

    sell_stocks_not_in_portfolio(context, data)

    LOG.info("rebalancing")
    LOG.info(context.stocks)
    totals = calculate_totals(context, data)
    LOG.info("totals calculated: %s" % totals)
    for stock, info in totals.items():
        order(stock, info["total"], style=LimitOrder(info["price"]))


def calculate_totals(context, data):
    totals = {}
    for stock, weight in context.stocks.items():
        price = data.current(stock, "price")
        limit = price + (price * 0.01)
        weight *= context.target_leverage
        total = floor((weight * context.portfolio.portfolio_value) / limit)
        totals[stock] = {"total": total, "price": limit}

    return totals


def sell_stocks_not_in_portfolio(context, data):
    for stock in context.portfolio.positions:
        if stock not in context.stocks:
            LOG.info(
                "selling stock %s that should no longer be in the portfolio"
                % stock.symbol
            )
            order_target_percent(stock, 0)


def cancel_all_orders(context, data):
    for _stock, orders in get_open_orders().items():
        for order in orders:
            cancel_order(order)

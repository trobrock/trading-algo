from math import floor, isnan
from pylivetrader.api import (
    schedule_function,
    date_rules,
    time_rules,
    get_datetime,
    get_open_orders,
    order,
    order_target_percent,
    cancel_order,
    symbol,
)
from pylivetrader.finance.execution import LimitOrder
import talib
import logbook

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


def before_trading_start(context, data):
    determine_market_direction(context, data)
    set_portfolio(context, data)


def determine_market_direction(context, data):
    history = data.history(symbol("QQQ"), fields="price", bar_count=390, frequency="1d")
    long_ma = talib.SMA(history, 200)

    if long_ma[-1] < history[-1]:
        LOG.info("market is up")
        context.direction = 1
    else:
        LOG.info("market is down")
        context.direction = -1


def set_portfolio(context, data):
    if context.direction == 1:
        context.stocks = {symbol("TMF"): 0.2, symbol("TYD"): 0.2, symbol("TQQQ"): 0.6}
    else:
        context.stocks = {symbol("TQQQ"): 1}


def rebalance(context, data):
    """Rebalance the portfolio based on context.stocks"""

    cancel_all_orders(context, data)
    sell_stocks_not_in_portfolio(context, data)

    LOG.info("rebalancing")
    LOG.info(context.stocks)
    totals = calculate_totals(context, data)
    LOG.info("totals calculated: %s" % totals)
    for stock, info in totals.items():
        order(stock, info["total"])


def calculate_totals(context, data):
    totals = {}
    for stock, weight in context.stocks.items():
        price = data.current(stock, "price")
        if isnan(price):
            # Pull the last week of minut data and use the last "price" for sparsely traded stocks
            price = data.history(stock, "price", bar_count=3360, frequency="1m")[-1]
        weight *= context.target_leverage
        total = floor((weight * context.portfolio.portfolio_value) / price)
        if stock in context.portfolio.positions:
            current = context.portfolio.positions[stock].amount
        else:
            current = 0
        totals[stock] = {"total": total - current, "price": price}

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
        for pending_order in orders:
            cancel_order(pending_order)

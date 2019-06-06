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
import logbook

LOG = logbook.Logger("algo")


def record(*args, **kwargs):
    """Records variables to the log"""
    LOG.info("args={}, kwargs={}".format(args, kwargs))


def initialize(context):
    """Sets up the context"""
    context.target_leverage = 1

    schedule_function(
        rebalance, date_rules.every_day(), time_rules.market_open(hours=2, minutes=20)
    )
    schedule_function(record_vars, date_rules.every_day(), time_rules.market_close())


def rebalance(context, data):
    stocks = {
        symbol("QQQ"): 0.35,
        symbol("PXMG"): 0.15,
        symbol("UUP"): 0.15,
        symbol("EDV"): 0.2,
        symbol("REZ"): 0.15,
    }

    for asset in context.portfolio.positions:
        if asset not in stocks:
            LOG.info(
                "selling %s because it's no longer in the portfolio" % asset.symbol
            )
            order_target_percent(asset, 0)

    for asset, allocation in stocks.items():
        order_target_percent(asset, allocation * context.target_leverage)


def record_vars(context, data):
    record(leverage=context.account.leverage)
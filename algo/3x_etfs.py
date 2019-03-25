from pylivetrader.api import (
                             schedule_function,
                             date_rules,
                             time_rules,
                             attach_pipeline,
                             get_datetime,
                             pipeline_output,
                             get_open_orders,
                             order,
                             cancel_order,
                             symbols
                             )
from zipline.pipeline import Pipeline
from pipeline_live.data.iex.pricing import USEquityPricing
from pipeline_live.data.iex.factors import SimpleMovingAverage, AverageDollarVolume, RSI
from pylivetrader.finance.execution import LimitOrder

# TODO: Move to pipeline once PR is merged https://github.com/alpacahq/pipeline-live/pull/6
# from pipeline_live.data.polygon.filters import StaticSymbols
import numpy as np
from zipline.pipeline.filters import CustomFilter
class StaticSymbols(CustomFilter):
    inputs = ()
    window_length = 1
    params = ('symbols',)

    def compute(self, today, assets, out, symbols, *inputs):
        ary = np.array([symbol in symbols for symbol in assets])
        out[:] = ary
# END TODO

from math import floor

import logbook
log = logbook.Logger('algo')

def record(*args, **kwargs):
    log.info('args={}, kwargs={}'.format(args, kwargs))

def initialize(context):
    context.ALLOW_SHORT = False
    # False => 754% on $1000 12/31/2016 => 10/31/2018

    schedule_function(my_rebalance, date_rules.every_day(), time_rules.market_open(minutes=5))
    schedule_function(my_record_vars, date_rules.every_day(), time_rules.market_close())

    my_pipe = make_pipeline()
    attach_pipeline(my_pipe, 'my_pipeline')

def make_pipeline():
    # My list of ETFS
    base_universe = StaticSymbols(symbols = ("DGAZ", "UGAZ", "JDST", "JNUG", "UWT", "DWT", "GUSH", "DRIP", "TQQQ", "SQQQ", "SPXS", "SPXL"))

    dollar_volume = AverageDollarVolume(
        window_length=14
    )
    high_dollar_volume = (dollar_volume > 10000000)

    mean_close_10 = SimpleMovingAverage(
        inputs=[USEquityPricing.close],
        window_length=10
    )
    mean_close_30 = SimpleMovingAverage(
        inputs=[USEquityPricing.close],
        window_length=30
    )
    rsi = RSI()

    percent_difference = (mean_close_10 - mean_close_30) / mean_close_30
    rsi_low = (rsi <= 40)
    rsi_high = (rsi >= 65)
    shorts = percent_difference.percentile_between(90, 100) & rsi_high
    longs = percent_difference.percentile_between(0, 15) & rsi_low

    securities_to_trade = high_dollar_volume

    return Pipeline(
        columns={
            'longs': longs,
            'shorts': shorts,
            'rsi': rsi,
            'percent_difference': percent_difference
        },
        screen=base_universe
    )

def compute_target_weights(context, data):
    weights = {}

    if context.longs and context.shorts:
        if context.ALLOW_SHORT:
            long_total = 0.5
            short_total = -0.5
        else:
            long_total = 1.0
            short_total = 0.0

        long_weight = long_total / len(context.longs)
        short_weight = short_total / len(context.shorts)
    else:
        return weights

    for security in context.portfolio.positions:
        if security not in context.longs and security not in context.shorts and data.can_trade(security):
            weights[security] = 0

    for security in context.longs:
        weights[security] = long_weight

    for security in context.shorts:
        weights[security] = short_weight

    return weights

def before_trading_start(context, data):
    log.info("RUNNING before_trading_start")
    # Prevent running more than once a day:
    # https://docs.alpaca.markets/platform-migration/zipline-to-pylivetrader/#deal-with-restart
    today = get_datetime().floor('1D')
    last_date = getattr(context, 'last_date', None)
    if today == last_date:
        log.info("Skipping before_trading_start because it's already ran today")
        return

    pipe_results = pipeline_output('my_pipeline')
    log.info(pipe_results)

    context.longs = []
    for sec in pipe_results[pipe_results['longs']].index.tolist():
        if data.can_trade(sec):
            context.longs.append(sec)

    context.shorts = []
    for sec in pipe_results[pipe_results['shorts']].index.tolist():
        if data.can_trade(sec):
            context.shorts.append(sec)

    # Track the last run
    context.last_date = today

def my_rebalance(context, data):
    target_weights = compute_target_weights(context, data)
    log.info(target_weights)

    if target_weights:
        portfolio_value = context.portfolio.portfolio_value

        for stock, weight in target_weights.items():
            current_price = data.current(stock, 'price')
            target_amount = floor((portfolio_value * weight) / current_price)

            if stock in context.portfolio.positions:
                current_amount = context.portfolio.positions[stock].amount
            else:
                current_amount = 0

            diff = target_amount - current_amount

            order(stock, diff, style=LimitOrder(current_price))

def my_record_vars(context, data):
    longs = shorts = 0
    for position in context.portfolio.positions.itervalues():
        if position.amount > 0:
            longs += 1
        elif position.amount < 0:
            shorts += 1

    record(
        leverage=context.account.leverage,
        long_count=longs,
        short_count=shorts
    )

def handle_data(context, data):
    for stock, position in context.portfolio.positions.items():
        if get_open_orders(stock):
            continue

        current_price = data.current(stock, 'price')
        cost_basis = position.cost_basis
        stop_loss = -0.05
        current_loss = (current_price - cost_basis) / cost_basis

        if current_loss < stop_loss:
            log.info('selling early %s (%.2f)' % (stock.symbol, current_loss))
            order(stock, position.amount * -1)

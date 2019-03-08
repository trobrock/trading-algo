from zipline.pipeline import Pipeline
from pylivetrader.api import (
                             schedule_function,
                             date_rules,
                             time_rules,
                             attach_pipeline,
                             get_datetime,
                             pipeline_output,
                             get_open_orders,
                             order,
                             cancel_order
                             )
from pipeline_live.data.iex.pricing import USEquityPricing
from pipeline_live.data.iex.fundamentals import IEXCompany, IEXKeyStats
from pipeline_live.data.iex.factors import SimpleMovingAverage, AverageDollarVolume
from pipeline_live.data.polygon.filters import IsPrimaryShareEmulation
from pylivetrader.finance.execution import LimitOrder

import logbook
log = logbook.Logger('algo')

import numpy as np  # needed for NaN handling
import math  # ceil and floor are useful for rounding
import dateutil

from itertools import cycle

def record(*args, **kwargs):
    log.info('args={}, kwargs={}'.format(args, kwargs))

def initialize(context):
    context.MaxCandidates = 100
    context.MaxBuyOrdersAtOnce = 50
    context.MyLeastPrice = 3.00
    context.MyMostPrice = 25.00
    context.MyFireSalePrice = context.MyLeastPrice
    context.MyFireSaleAge = 6
    context.buy_factor = .99
    context.sell_factor = 1.01

    context.MaxInvestment = 150000

    # over simplistic tracking of position age
    if not hasattr(context, 'age') or not context.age:
        context.age = {}

    # Rebalance
    minutes = 10
    trading_hours = 6.5
    trading_minutes = int(trading_hours * 60)
    for minutez in range(1, trading_minutes, minutes):
        schedule_function(my_rebalance,
                date_rules.every_day(),
                time_rules.market_open(
                    minutes=minutez))

    # Prevent excessive logging of canceled orders at market close.
    schedule_function(
        cancel_open_orders,
        date_rules.every_day(),
        time_rules.market_close(
            hours=0,
            minutes=1))

    # Record variables at the end of each day.
    schedule_function(
        my_record_vars,
        date_rules.every_day(),
        time_rules.market_close())

    # Create our pipeline and attach it to our algorithm.
    my_pipe = make_pipeline(context)
    attach_pipeline(my_pipe, 'my_pipeline')

def make_pipeline(context):
    """
    Create our pipeline.
    """

    # Filter for primary share equities. IsPrimaryShare is a built-in filter.
    primary_share = IsPrimaryShareEmulation()

    # Equities listed as common stock (as opposed to, say, preferred stock).
    # 'ST00000001' indicates common stock.
    common_stock = IEXCompany.issueType.latest.eq('cs')

    # Equities not trading over-the-counter.
    not_otc = ~IEXCompany.exchange.latest.startswith(
        'OTC')

    # Not when-issued equities.
    not_wi = ~IEXCompany.symbol.latest.endswith('.WI')

    # Equities without LP in their name, .matches does a match using a regular
    # expression
    not_lp_name = ~IEXCompany.companyName.latest.matches(
        '.* L[. ]?P.?$')

    # Equities whose most recent Morningstar market cap is not null have
    # fundamental data and therefore are not ETFs.
    have_market_cap = IEXKeyStats.marketcap.latest.notnull()

    # At least a certain price
    price = USEquityPricing.close.latest
    AtLeastPrice = (price >= context.MyLeastPrice)
    AtMostPrice = (price <= context.MyMostPrice)

    # Filter for stocks that pass all of our previous filters.
    tradeable_stocks = (
        primary_share
        & common_stock
        & not_otc
        & not_wi
        & not_lp_name
        & have_market_cap
        & AtLeastPrice
        & AtMostPrice
    )

    LowVar = 6
    HighVar = 40

    log.info(
        '''
Algorithm initialized variables:
 context.MaxCandidates %s
 LowVar %s
 HighVar %s''' %
        (context.MaxCandidates, LowVar, HighVar))

    # High dollar volume filter.
    base_universe = AverageDollarVolume(
        window_length=20,
        mask=tradeable_stocks
    ).percentile_between(LowVar, HighVar)

    # Short close price average.
    ShortAvg = SimpleMovingAverage(
        inputs=[USEquityPricing.close],
        window_length=3,
        mask=base_universe
    )

    # Long close price average.
    LongAvg = SimpleMovingAverage(
        inputs=[USEquityPricing.close],
        window_length=45,
        mask=base_universe
    )

    percent_difference = (ShortAvg - LongAvg) / LongAvg

    # Filter to select securities to long.
    stocks_worst = percent_difference.bottom(context.MaxCandidates)
    securities_to_trade = (stocks_worst)

    return Pipeline(
        columns={
            'stocks_worst': stocks_worst
        },
        screen=(securities_to_trade),
    )

def before_trading_start(context, data):
    log.info("RUNNING before_trading_start")
    # Prevent running more than once a day:
    # https://docs.alpaca.markets/platform-migration/zipline-to-pylivetrader/#deal-with-restart
    today = get_datetime().floor('1D')
    last_date = getattr(context, 'last_date', None)
    if today == last_date:
        log.info("Skipping before_trading_start because it's already ran today")
        return

    context.output = pipeline_output('my_pipeline')

    context.stocks_worst = context.output[context.output['stocks_worst']].index.tolist()
    context.MyCandidate = cycle(context.stocks_worst)

    # Update ages
    for stock in context.portfolio.positions:
        if stock in context.age:
            context.age[stock] += 1
        else:
            log.info("Could not find %s in context.age" % stock.symbol)
            context.age[stock] = 1

    # Remove stale ages
    for stock in context.age:
        if stock not in context.portfolio.positions:
            del context.age[stock]

    # Track the last run
    context.last_date = today

def my_rebalance(context, data):
    cancel_open_buy_orders(context, data)

    # Order sell at profit target in hope that somebody actually buys it
    for stock in context.portfolio.positions:
        submit_sell(stock, context, data)

    weight = float(1.00 / context.MaxBuyOrdersAtOnce)
    for ThisBuyOrder in range(context.MaxBuyOrdersAtOnce):
        submit_buy(context.MyCandidate.__next__(), context, data, weight)

def submit_sell(stock, context, data):
    if get_open_orders(stock):
        return

    # We bought a stock but don't know it's age yet
    if stock not in context.age:
        context.age[stock] = 0

    # Don't sell stuff that's less than 1 day old
    if stock in context.age and context.age[stock] < 1:
        return

    shares = context.portfolio.positions[stock].amount
    current_price = float(data.current([stock], 'price'))
    cost_basis = float(context.portfolio.positions[stock].cost_basis)

    if (context.age[stock] >= context.MyFireSaleAge and
            (current_price < context.MyFireSalePrice or current_price < cost_basis)):
        log.info("%s is in fire sale!" % stock.symbol)
        sell_price = float(make_div_by_05(.95 * current_price, buy=False))

        order(stock, -shares, style=LimitOrder(sell_price))
    else:
        sell_price = float(
            make_div_by_05(
                cost_basis *
                context.sell_factor,
                buy=False))

        order(stock, -shares, style=LimitOrder(sell_price))

def submit_buy(stock, context, data, weight):
    cash = min(investment_limits(context)['remaining_to_invest'], context.portfolio.cash)

    price_history = data.history([stock], 'price', 20, '1d')
    average_price = float(price_history.mean())
    current_price = float(data.current([stock], 'price'))

    if np.isnan(current_price):
        pass  # probably best to wait until nan goes away
    else:
        if current_price > float(1.25 * average_price): # if the price is 25% above the 20d avg
            buy_price = float(current_price)
        else: # Otherwise buy at a discount
            buy_price = float(current_price * context.buy_factor)
        buy_price = float(make_div_by_05(buy_price, buy=True))
        shares_to_buy = int(weight * cash / buy_price)
        max_exposure = int(weight * context.portfolio.portfolio_value / buy_price)

        # Prevent over exposing to a particular stock, never own more than 1/max_buy_orders
        # of our account value
        positions = context.portfolio.positions
        if stock in positions and positions[stock].amount >= max_exposure:
            return

        # This cancels open sales that would prevent these buys from being submitted if running
        # up against the PDT rule
        open_orders = get_open_orders()
        if stock in open_orders:
            for open_order in open_orders[stock]:
                cancel_order(open_order)

        order(stock, shares_to_buy, style=LimitOrder(buy_price))

def make_div_by_05(s, buy=False):
    s *= 20.00
    s = math.floor(s) if buy else math.ceil(s)
    s /= 20.00
    return s

def my_record_vars(context, data):
    """
    Record variables at the end of each day.
    """

    # Record our variables.
    record(leverage=context.account.leverage)

    if 0 < len(context.age):
        MaxAge = context.age[max(
            context.age.keys(), key=(lambda k: context.age[k]))]
        MinAge = context.age[min(
            context.age.keys(), key=(lambda k: context.age[k]))]
        record(MaxAge=MaxAge)
        record(MinAge=MinAge)

    limits = investment_limits(context)
    record(ExcessCash=limits['excess_cash'])
    record(Invested=limits['invested'])
    record(RemainingToInvest=limits['remaining_to_invest'])

def cancel_open_buy_orders(context, data):
    oo = get_open_orders()
    if len(oo) == 0:
        return
    for stock, orders in oo.items():
        for o in orders:
            # message = 'Canceling order of {amount} shares in {stock}'
            # log.info(message.format(amount=o.amount, stock=stock))
            if 0 < o.amount:  # it is a buy order
                cancel_order(o)


def cancel_open_orders(context, data):
    oo = get_open_orders()
    if len(oo) == 0:
        return
    for stock, orders in oo.items():
        for o in orders:
            # message = 'Canceling order of {amount} shares in {stock}'
            # log.info(message.format(amount=o.amount, stock=stock))
            cancel_order(o)

def investment_limits(context):
    cash = context.portfolio.cash
    portfolio_value = context.portfolio.portfolio_value
    invested = portfolio_value - cash
    remaining_to_invest = max(0, context.MaxInvestment - invested)
    excess_cash = max(0, cash - remaining_to_invest)

    return {
        "invested": invested,
        "remaining_to_invest": remaining_to_invest,
        "excess_cash": excess_cash
    }

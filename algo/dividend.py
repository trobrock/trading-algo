from pylivetrader.api import (
    schedule_function,
    date_rules,
    time_rules,
    attach_pipeline,
    get_datetime,
    pipeline_output,
    get_open_orders,
    order,
    order_target_percent,
    cancel_order,
    symbols,
)

from pipeline_live.data.alpaca.factors import AverageDollarVolume
from pipeline_live.data.alpaca.pricing import USEquityPricing
from pipeline_live.data.polygon.fundamentals import PolygonCompany

from zipline.pipeline import Pipeline
from zipline.pipeline.factors import CustomFactor
import numpy as np

import alpaca_trade_api as tradeapi
from pipeline_live.data.sources.util import parallelize, daily_cache

import os
import logbook

log = logbook.Logger("algo")


def list_symbols():
    return [
        a.symbol
        for a in tradeapi.REST().list_assets()
        if a.tradable and a.status == "active"
    ]


def polygon_dividends():
    all_symbols = list_symbols()
    return _polygon_dividend(all_symbols)


@daily_cache(filename="polygon_dividends.pkl")
def _polygon_dividend(all_symbols):
    def fetch(symbols):
        api = tradeapi.REST()
        params = {"symbols": ",".join(symbols)}
        return api.polygon.get("/meta/symbols/dividends", params=params)

    return parallelize(fetch, workers=25, splitlen=50)(all_symbols)


class DividendYield(CustomFactor):
    window_length = 1
    inputs = [USEquityPricing.close]

    def compute(self, today, assets, out, close):
        dividends = polygon_dividends()
        out[:] = (
            np.array(
                [
                    sum([div.get("amount") for div in dividends[asset][0:4]])
                    if asset in dividends
                    else 0
                    for asset in assets
                ]
            )
            / close
        )


def initialize(context):
    attach_pipeline(my_pipeline(context), "my_pipeline")

    schedule_function(
        rebalance,
        date_rule=date_rules.every_day(),
        time_rule=time_rules.market_open(
            hours=int(os.environ["HOURS"]), minutes=int(os.environ["MINUTES"])
        ),
    )


def before_trading_start(context, data):
    log.info("running pipeline")
    context.output = pipeline_output("my_pipeline")
    log.info("done")


def my_pipeline(context):
    pipe = Pipeline()

    dollar_volume = AverageDollarVolume(window_length=20)
    minimum_volume = dollar_volume > 100000

    mkt_cap = PolygonCompany.marketcap.latest
    mkt_cap_top_500 = mkt_cap.top(20)

    equity_price = USEquityPricing.close.latest
    over_two = equity_price > 2

    Dividend_Factor = DividendYield()
    pipe.add(Dividend_Factor, "DividendYield")

    pipe.add(USEquityPricing.close.latest, "price")

    Pays_Dividend = Dividend_Factor > 0.0
    pipe.set_screen(minimum_volume & mkt_cap_top_500 & over_two & Pays_Dividend)

    return pipe


def rebalance(context, data):
    allocation = 1.0 / len(context.output)
    log.info("per stock allocation: %.4f" % allocation)

    log.info("selling stocks no longer in mix")
    for asset in context.portfolio.positions:
        if asset not in context.output.index:
            order_target_percent(asset, 0)

    log.info("rebalancing...")
    for asset, row in context.output.iterrows():
        shares = calculate_order(context, asset, allocation, row["price"])
        order(asset, shares)

    log.info("done")


def calculate_order(context, asset, allocation, price):
    portfolio_total = context.portfolio.portfolio_value

    shares_total = round((portfolio_total * allocation) / price)
    current_shares = (
        context.portfolio.positions[asset]["amount"]
        if asset in context.portfolio.positions
        else 0
    )

    return shares_total - current_shares

from pylivetrader.api import (
    schedule_function,
    date_rules,
    time_rules,
    attach_pipeline,
    get_datetime,
    pipeline_output,
    get_open_orders,
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
        time_rule=time_rules.market_close(minutes=15),
    )


def my_pipeline(context):
    pipe = Pipeline()

    dollar_volume = AverageDollarVolume(window_length=20)
    minimum_volume = dollar_volume > 100000

    mkt_cap = PolygonCompany.marketcap.latest
    mkt_cap_top_500 = mkt_cap.top(100)

    equity_price = USEquityPricing.close.latest
    over_two = equity_price > 2

    Dividend_Factor = DividendYield()
    pipe.add(Dividend_Factor, "DividendYield")

    Pays_Dividend = Dividend_Factor > 0.0
    pipe.set_screen(minimum_volume & mkt_cap_top_500 & over_two & Pays_Dividend)

    return pipe


def rebalance(context, data):
    context.output = pipeline_output("my_pipeline")

    allocation = 1.0 / len(context.output)
    longs = context.output.index.tolist()

    for asset in context.portfolio.positions:
        if asset not in longs:
            order_target_percent(asset, 0)

    for asset in longs:
        order_target_percent(asset, allocation)

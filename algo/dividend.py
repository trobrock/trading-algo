from pylivetrader.api import (
    schedule_function,
    date_rules,
    time_rules,
    attach_pipeline,
    pipeline_output,
    order,
    symbol,
)

from pipeline_live.data.alpaca.factors import AverageDollarVolume
from pipeline_live.data.alpaca.pricing import USEquityPricing
from pipeline_live.data.polygon.fundamentals import PolygonCompany
from pipeline_live.data.polygon.filters import IsPrimaryShareEmulation

from zipline.pipeline import Pipeline
from zipline.pipeline.factors import CustomFactor, Returns
import numpy as np
import pandas as pd

import alpaca_trade_api as tradeapi
from pipeline_live.data.sources.util import parallelize

import os
import logbook

log = logbook.Logger("algo")


def financials(symbols):
    def fetch(symbols):
        symbol = symbols[0]
        api = tradeapi.REST()
        data = api.polygon.get(
            "/reference/financials/{}".format(symbol), version="v2", params={"limit": 1}
        )
        return {symbol: data["results"]}

    return parallelize(fetch, workers=25, splitlen=1)(symbols)


def dividends(symbols):
    def fetch(symbols):
        symbol = symbols[0]
        api = tradeapi.REST()
        data = api.polygon.get(
            "/reference/dividends/{}".format(symbol), version="v2", params={"limit": 1}
        )
        return {symbol: data["results"]}

    return parallelize(fetch, workers=25, splitlen=1)(symbols)


class DividendYield(CustomFactor):
    window_length = 1
    inputs = []

    def compute(self, today, assets, out, *inputs):
        asset_financials = financials(assets)
        out[:] = np.array(
            [
                asset_financials[asset][0].get("dividendYield", 0)
                if asset_financials[asset]
                else 0
                for asset in assets
            ]
        )


class PriceEarningsRatio(CustomFactor):
    window_length = 1
    inputs = []

    def compute(self, today, assets, out, *inputs):
        asset_financials = financials(assets)
        out[:] = np.array(
            [
                asset_financials[asset][0].get("priceToEarningsRatio", 0)
                if asset_financials[asset]
                else 0
                for asset in assets
            ]
        )


def print_report(context, data):
    data_rows = []

    for s, d in dividends(
        [asset.symbol for asset in context.portfolio.positions]
    ).items():
        asset = symbol(s)
        data_rows.append(
            (
                asset.symbol,
                d[0]["amount"],
                d[0]["paymentDate"],
                data.history([asset], "price", 5, "1d").values[-1][0],
            )
        )

    # Symbol, EX Date, Payment Date, Amount, Current Yield, Time To Payment
    portfolio = pd.DataFrame(
        data_rows,
        columns=["asset", "dividend_amount", "dividend_payment_date", "price"],
    ).set_index(["asset"])

    portfolio["current_yield"] = (
        portfolio["dividend_amount"] * 4 / portfolio["price"]
    ) * 100
    log.info(portfolio.sort_values("dividend_payment_date"))


def initialize(context):
    attach_pipeline(my_pipeline(context), "my_pipeline")

    schedule_function(
        rebalance,
        date_rule=date_rules.every_day(),
        time_rule=time_rules.market_open(
            hours=int(os.environ["HOURS"]), minutes=int(os.environ["MINUTES"])
        ),
    )
    schedule_function(
        print_report,
        date_rule=date_rules.every_day(),
        time_rule=time_rules.market_close(),
    )


def before_trading_start(context, data):
    log.info("running pipeline")
    output = pipeline_output("my_pipeline")
    output.sort_values("rank", ascending=False, inplace=True)
    context.output = output
    log.info("done")


def my_pipeline(context):
    pipe = Pipeline()

    mkt_cap = PolygonCompany.marketcap.latest
    mkt_cap_top_500 = mkt_cap.top(500)

    dollar_volume = AverageDollarVolume(window_length=20, mask=mkt_cap_top_500)
    minimum_volume = dollar_volume > 100000

    equity_price = USEquityPricing.close.latest
    over_two = equity_price > 2

    base_universe = (
        IsPrimaryShareEmulation() & minimum_volume & mkt_cap_top_500 & over_two
    )

    returns = Returns(
        inputs=[USEquityPricing.close], mask=base_universe, window_length=90
    )

    dividend_yield = DividendYield(mask=base_universe)
    pipe.add(dividend_yield, "dividend_yield")
    pipe.add(returns, "returns")
    pipe.add((returns.rank() * dividend_yield.rank()).rank(), "rank")

    pe_ratio = PriceEarningsRatio(mask=base_universe)
    pipe.add(pe_ratio, "pe_ratio")

    pipe.add(USEquityPricing.close.latest, "price")

    good_pe_ratio = (pe_ratio > 0) & (pe_ratio <= 20)
    pays_dividend = dividend_yield > 0.0
    pipe.set_screen(base_universe & pays_dividend & good_pe_ratio)

    return pipe


def rebalance(context, data):
    log.info("rebalancing...")

    portfolio_total = context.portfolio.portfolio_value
    portfolio = {}
    prices = {}

    for asset, position in context.portfolio.positions.items():
        portfolio[asset] = position.amount

    for new_asset, row in context.output.head(20).iterrows():
        if new_asset in portfolio:
            log.info("already have {}, skipping".format(new_asset.symbol))
            continue

        log.info("trying to add {}".format(new_asset.symbol))

        potential_portfolio = portfolio.copy()
        potential_portfolio[new_asset] = 0

        target_allocation = 0.95 / len(potential_portfolio)  # Leave 5% cash buffer
        log.info("target allocation: %.4f" % target_allocation)

        for asset, current_shares in potential_portfolio.items():
            if asset in prices:
                price = prices[asset]
            else:
                price = data.history([asset], "price", 5, "1d").values[-1][0]
                prices[asset] = price

            shares = round((portfolio_total * target_allocation) / price)

            log.info(
                "asset: {}, shares: {}, current_shares: {}, price: {}".format(
                    asset.symbol, shares, current_shares, price
                )
            )

            potential_portfolio[asset] = max(current_shares, shares)

        print_portfolio(potential_portfolio)

        if potential_portfolio[new_asset] > 0:
            log.info("keeping {}".format(new_asset.symbol))
            portfolio = potential_portfolio

    print_portfolio(portfolio)

    for asset, shares in portfolio.items():
        current_shares = (
            context.portfolio.positions[asset]["amount"]
            if asset in context.portfolio.positions
            else 0
        )
        share_diff = shares - current_shares
        if share_diff:
            log.info("buying {} shares of {}".format(share_diff, asset.symbol))
            order(asset, share_diff)

    log.info("done")


def print_portfolio(portfolio):
    log.info("*" * 50)

    for asset, shares in portfolio.items():
        log.info("{}: {}".format(asset.symbol, shares))

    log.info("*" * 50)

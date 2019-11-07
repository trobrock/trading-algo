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


class PortfolioOptimizer(object):
    CASH_BUFFER = 0.05

    def __init__(
        self, portfolio_value, cash_available, current_portfolio, get_price_fn
    ):
        self.portfolio_value = portfolio_value
        self.cash_available = cash_available
        self.current_portfolio = current_portfolio
        self.new_portfolio = current_portfolio.copy()
        self.get_price_fn = get_price_fn
        self.price_cache = {}

        self.load_prices()
        self.rebalance(self.new_portfolio.copy())

    def add(self, stock):
        if stock in self.current_portfolio:
            return True

        attempted_portfolio = self.new_portfolio.copy()
        attempted_portfolio[stock] = 0
        log.info(f"attempting to add {stock} to portfolio @ {self.get_price(stock)}")

        return self.rebalance(attempted_portfolio)

    def rebalance(self, attempted_portfolio):
        target_allocation = (1 - self.CASH_BUFFER) / len(attempted_portfolio)
        cost_of_update = 0.0
        log.info(f"target_allocation: {target_allocation}")

        for stock, current_shares in attempted_portfolio.items():
            target_shares = round(
                (self.portfolio_value * target_allocation) / self.get_price(stock)
            )

            if target_shares <= current_shares:
                continue

            cost_of_update += (target_shares - current_shares) * self.get_price(stock)
            log.info(
                f"[{stock}] cost_of_update:{cost_of_update} cash_available:{self.cash_available}"
            )
            attempted_portfolio[stock] = target_shares

        attempted_portfolio = {
            stock: quantity
            for stock, quantity in attempted_portfolio.items()
            if quantity > 0
        }
        if (
            attempted_portfolio != self.new_portfolio
            and cost_of_update <= self.cash_available
        ):
            log.info(f"found ideal portfolio: {attempted_portfolio}")
            self.cash_available -= cost_of_update
            self.new_portfolio = attempted_portfolio
            return True
        else:
            return False

    def optimizations(self):
        return {
            stock: target_amount - self.current_portfolio.get(stock, 0)
            for stock, target_amount in self.new_portfolio.items()
            if target_amount - self.current_portfolio.get(stock, 0) > 0
        }

    def get_price(self, stock):
        if stock in self.price_cache:
            return self.price_cache[stock]

        price = self.get_price_fn(stock)
        if price:
            self.price_cache[stock] = price

    def load_prices(self):
        for stock in self.current_portfolio.keys():
            self.get_price(stock)


def get_price_fn(data):
    return lambda stock: data.history([symbol(stock)], "price", 5, "1d").values[-1][0]


def rebalance(context, data):
    log.info("rebalancing...")

    current_portfolio = {
        asset.symbol: position.amount
        for asset, position in context.portfolio.positions.items()
    }
    optimizer = PortfolioOptimizer(
        context.portfolio.portfolio_value,
        context.portfolio.cash,
        current_portfolio,
        get_price_fn(data),
    )

    for new_asset, row in context.output.head(20).iterrows():
        optimizer.add(new_asset.symbol)

    validate(context, data, optimizer)

    for stock, quantity in optimizer.optimizations().items():
        order(symbol(stock), quantity)

    log.info("done")


def validate(context, data, optimizer):
    log.info(f"found optimizations: {optimizer.optimizations()}")
    cost_of_update = sum(
        [
            amount * get_price_fn(data)(stock)
            for stock, amount in optimizer.optimizations().items()
        ]
    )
    log.info(
        f"cash_available:{context.portfolio.cash} needed_for_change:{cost_of_update}"
    )

    dollar_value = {}
    total = 0.0
    for stock, quantity in optimizer.new_portfolio.items():
        dollar_value[stock] = quantity * get_price_fn(data)(stock)
        total += dollar_value[stock]

    for stock, dollars in dollar_value.items():
        log.info(f"{stock}: {(dollars / total) * 100}%")

    log.info("done with validation")


def print_portfolio(portfolio):
    log.info("*" * 50)

    for asset, shares in portfolio.items():
        log.info("{}: {}".format(asset.symbol, shares))

    log.info("*" * 50)

# Algo Trading w/ pylivetrader and heroku

The purpose of this repo is to provide a framework that will enable you to launch your own (or the
pre-built) algorithms developed using Quantopian's Zipline API onto Heroku.

First, if you don't know what Heroku is, it is a host platform that makes it super simple to launch
an application and in the case of these algorithms, would even be free.

## Pre-reqs

* Currently this is designed around an OSX operating system, I don't have access to a windows
platform, but would welcome the contributions.
* You must have Git installed, on OSX it should come pre-installed or you can install the latest
version with Homebrew (https://brew.sh)
* You must have Heroku command line tools installed (https://devcenter.heroku.com/articles/heroku-cli)
* You must have created an account with Alpaca (https://alpaca.markets), you will need an API key
from them to launch your algo.

## How to use this

1) Open a Terminal window and change directories to where ever you want this code to live with your
algorithms
2) Run `bash <(curl -fsSL https://raw.githubusercontent.com/trobrock/trading-algo/master/get-started)`
3) This will walk you through the process of downloading all required code, creating the heroku app,
and deploying the algorithm. You are able to supply your own algorithm during this process, but you
must make sure that it runs within the pylivetrader environment on python3, you can read more about
migrating from Quantopian here (https://github.com/alpacahq/pylivetrader/blob/master/migration.md)
4) Profit... literally!

## Contributing

This is just the beginning of this project and I'd like to move it towards the full framework to
make algo trading more accessible to those with less experience with operations and software eng,
this includes tooling to convert quantopian algos to pylivetrader, etc.

If you are interested in contributing, please submit a PR and I will review as fast as possible. If
you are having issues, please file one here and include as much information as possible.

# BSC 持仓看板

This local web application tracks BSC tokens that have Binance Futures contracts and organizes them into low-FDV discovery, replenishment, holding reconciliation, and local maintenance views.

## Run

The application uses only the Python standard library and the project virtual environment.

~~~bash
.venv/bin/python dashboard_server.py --open
~~~

## Start and Stop

Use the shell scripts to run the dashboard in the background. `start.sh` creates the project virtual environment when it is missing, records only its own process ID, and writes runtime output to `.run/dashboard.log`.

~~~bash
./start.sh
./stop.sh
~~~

The default listener is `127.0.0.1:8765`. Override the port or host when needed:

~~~bash
DASHBOARD_PORT=8767 ./start.sh
DASHBOARD_HOST=0.0.0.0 ./start.sh
~~~

The `0.0.0.0` host exposes wallet addresses and portfolio data to the reachable network. Use it only behind trusted network controls.

The server listens on 127.0.0.1:8765 by default. It stores local configuration, snapshots, token rules, and manually maintained exchange holdings in data/dashboard.sqlite3.

Click **刷新市场与链上余额** to fetch current Binance Alpha metadata and Binance Futures prices. When wallet addresses are configured, it also fetches BSC ERC-20 balances for every configured wallet. The browser does not call Binance or BSC RPC services directly.

## Configuration

The **设置** view manages:

- Optional BSC wallet addresses, one address per line. A new installation does not include any wallet address.
- Default initial purchase amount in USD.
- Default replenishment target in USD.
- Low-FDV threshold in USD.
- Price-gap warning threshold.
- Daily scheduled refresh time in Asia/Singapore.

Each token can override the initial purchase amount, replenishment target, replenishment inclusion, exclusion status, and note.

When no wallet address is configured, refreshes still retain market snapshots and treat every on-chain holding amount as zero.

The server checks the configured time every 30 seconds while it is running. It creates at most one successful scheduled refresh per Singapore calendar day and retries after a failed scheduled refresh. If the server is stopped, it cannot refresh in the background; the next startup performs the missed refresh after the configured time.

## Snapshot History

Every manual or scheduled refresh creates a new snapshot. Historical snapshots are retained in the local database.

The **持币地址变化** view compares the latest snapshot from each selected Singapore date. It compares the Alpha holders count for tokens that exist on both dates and have a count on both dates. Rows are sorted by the absolute holder-count change, descending. Tokens with missing holder counts are reported separately and are never interpreted as zero.

## Local Exchange Holdings

The application does not use exchange APIs. Add exchange holdings manually in **本地维护**, or paste CSV data with these columns:

~~~csv
source_name,asset_symbol,amount,contract_address,mapping_status,note
Binance,EXAMPLE,12.5,0x0000000000000000000000000000000000000000,confirmed,optional note
~~~

Required columns are source_name, asset_symbol, and amount. mapping_status=confirmed requires a BSC contract address. A pending record is not included in total holding value or replenishment decisions. When a pending exchange Symbol has exactly one current token candidate, the UI offers a confirmation action; it never confirms a Symbol-only match automatically.

CSV imports update a record when source, exchange Symbol, contract address, and mapping status are the same. This prevents duplicate imports from increasing holdings.

## Decision Rules

- **低 FDV 未记录**: FDV is at or below the configured threshold and the token has neither a BSC balance nor a confirmed locally maintained exchange balance.
- **交易所待确认**: A local exchange record exists but its association with a BSC contract has not been confirmed. It is excluded from discovery and replenishment conclusions.
- **补仓清单**: The token is held, is below its configured target value, is at or below the low-FDV threshold, is not excluded, and has no pending exchange record for the same Symbol.
- **总持仓价值**: Confirmed BSC and local exchange quantities multiplied by the Alpha token price.

The source provides FDV. The dashboard labels this field as FDV and does not treat it as verified circulating market capitalization.

## Test

~~~bash
.venv/bin/python -m unittest discover -s tests -v
~~~

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
- Optional username/password login protection, disabled by default.

Each token can override the initial purchase amount, replenishment target, replenishment inclusion, exclusion status, and note.

When no wallet address is configured, refreshes still retain market snapshots and treat every on-chain holding amount as zero.

The server checks the configured time every 30 seconds while it is running. It creates at most one successful scheduled refresh per Singapore calendar day and retries after a failed scheduled refresh. If the server is stopped, it cannot refresh in the background; the next startup performs the missed refresh after the configured time.

## Login Protection

The **设置** view includes **访问保护**. It is disabled in a new installation. Enabling it requires a username and a password of 8 to 256 characters. The server stores a PBKDF2-SHA256 derived password value with a random salt; the plaintext password is not stored or returned by the API.

When enabled, all dashboard data, refresh, configuration, holding maintenance, and historical comparison APIs require a login session. Sessions expire after 12 hours, logging out removes the current session, and changing access protection settings invalidates other active sessions. Scheduled refreshes continue to run on the server without a browser session.

The default `127.0.0.1` listener keeps credentials and portfolio data on the local machine. If the dashboard is exposed with `DASHBOARD_HOST=0.0.0.0`, place it behind HTTPS and trusted network controls. Plain HTTP does not protect passwords or session cookies while they travel over a network.

## Snapshot History

Every manual or scheduled refresh creates a new snapshot. Historical snapshots are retained in the local database.

The **持币地址变化** view compares the latest snapshot from each selected Singapore date. It compares the Alpha holders count for tokens that exist on both dates and have a count on both dates. Rows are sorted by the absolute holder-count change, descending. Tokens with missing holder counts are reported separately and are never interpreted as zero.

## Local Exchange Holdings

The application does not use exchange APIs. Add exchange holdings manually in **本地维护**, or paste CSV data with these columns:

~~~csv
source_name,asset_symbol,amount,contract_address,note
Binance,EXAMPLE,12.5,0x0000000000000000000000000000000000000000,optional note
~~~

Required columns are source_name, asset_symbol, and amount. When contract_address is supplied, the record is confirmed with that BSC contract. When it is blank, the dashboard matches asset_symbol against the latest BSC token snapshot: one candidate is confirmed automatically; zero or multiple candidates remain pending for manual selection. Pending records are excluded from holding value and replenishment decisions. A refresh also confirms historical pending records that have exactly one current candidate.

CSV imports update a record when source, exchange Symbol, resolved contract address, and resolved matching status are the same. This prevents duplicate imports from increasing holdings.

## Decision Rules

- **未持仓代币**: The BSC balance and confirmed locally maintained exchange balance are both zero, and no exchange record is awaiting confirmation. This inventory is not limited by the FDV threshold or per-token exclusion setting.
- **低 FDV 未记录**: FDV is at or below the configured threshold and the token has neither a BSC balance nor a confirmed locally maintained exchange balance.
- **交易所待确认**: A local exchange record exists but its association with a BSC contract has not been confirmed. It is excluded from discovery and replenishment conclusions.
- **补仓清单**: The token is held, is below its configured target value, is at or below the low-FDV threshold, is not excluded, and has no pending exchange record for the same Symbol.
- **总持仓价值**: Confirmed BSC and local exchange quantities multiplied by the Alpha token price.

The source provides FDV. The dashboard labels this field as FDV and does not treat it as verified circulating market capitalization.

## Test

~~~bash
.venv/bin/python -m unittest discover -s tests -v
~~~

#!/usr/bin/env python3
from __future__ import annotations

import json
import ssl
import sys
import time
import argparse
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path
from typing import Any


LIST_API = "https://www.binance.com/bapi/defi/v1/public/wallet-direct/buw/wallet/cex/alpha/all/token/list"
FUTURES_API = "https://fapi.binance.com/fapi/v1/exchangeInfo"
FUTURES_TICKER_API = "https://fapi.binance.com/fapi/v1/ticker/price"
SPOT_TICKER_API = "https://api.binance.com/api/v3/ticker/price"
BSC_RPC_URL = "https://bsc-dataseed.binance.org/"
MILLION = Decimal("1000000")
OUTPUT_DIR = Path(__file__).resolve().parent / "outputs"
RUN_DATE = date.today().isoformat()
XLSX_OUTPUT_PATH = OUTPUT_DIR / f"bsc_futures_by_fdv_{RUN_DATE}.xlsx"

USER_AGENT = "Mozilla/5.0 (compatible; bsc-fdv-list/1.0)"
SPOT_QUOTE_PRIORITY = ("USDT", "USDC", "FDUSD", "BNB", "BTC", "ETH")
FUTURES_QUOTE_PRIORITY = ("USDT", "USDC", "FDUSD", "BUSD")


@dataclass(frozen=True)
class TokenMatch:
    rank: int
    symbol: str
    name: str
    futures_symbol: str
    fdv: Decimal
    token_price: Decimal
    market_price: Decimal | None
    price_gap_pct: Decimal | None
    contract_address: str
    match_method: str
    token_decimals: int
    holder_count: int | None
    holding_amount: Decimal


def fetch_json(url: str) -> Any:
    last_error: Exception | None = None

    for attempt in range(3):
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError:
            raise
        except (urllib.error.URLError, TimeoutError, ssl.SSLError) as error:
            last_error = error
            if attempt < 2:
                time.sleep(1 + attempt)

    assert last_error is not None
    raise last_error


def post_json(url: str, payload: Any) -> Any:
    last_error: Exception | None = None
    body = json.dumps(payload).encode("utf-8")

    for attempt in range(3):
        request = urllib.request.Request(
            url,
            data=body,
            headers={"User-Agent": USER_AGENT, "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError:
            raise
        except (urllib.error.URLError, TimeoutError, ssl.SSLError) as error:
            last_error = error
            if attempt < 2:
                time.sleep(1 + attempt)

    assert last_error is not None
    raise last_error


def parse_decimal(value: Any, default: str = "0") -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(default)


def parse_optional_int(value: Any) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def load_bsc_tokens() -> list[dict[str, Any]]:
    payload = fetch_json(LIST_API)
    tokens = payload.get("data", [])
    filtered_tokens: list[dict[str, Any]] = []

    for token in tokens:
        if str(token.get("chainId")) != "56":
            continue
        if bool(token.get("stockState")):
            continue
        if bool(token.get("offline")):
            continue
        if bool(token.get("listingCex")) and bool(token.get("cexOffDisplay")):
            continue

        filtered_tokens.append(token)

    return filtered_tokens


def load_futures_symbols() -> tuple[set[str], dict[str, list[str]]]:
    payload = fetch_json(FUTURES_API)
    base_assets: set[str] = set()
    symbols_by_base: dict[str, list[str]] = defaultdict(list)

    for symbol_info in payload.get("symbols", []):
        if symbol_info.get("status") != "TRADING":
            continue

        base_asset = str(symbol_info.get("baseAsset", "")).upper()
        symbol = str(symbol_info.get("symbol", "")).upper()
        if not base_asset or not symbol:
            continue

        base_assets.add(base_asset)
        symbols_by_base[base_asset].append(symbol)

    return base_assets, symbols_by_base


def load_futures_prices() -> dict[str, Decimal]:
    payload = fetch_json(FUTURES_TICKER_API)
    prices: dict[str, Decimal] = {}
    for item in payload:
        symbol = str(item.get("symbol", "")).upper()
        if symbol:
            prices[symbol] = parse_decimal(item.get("price"))
    return prices


def pick_preferred_futures_symbol(symbols: list[str]) -> str:
    def sort_key(symbol: str) -> tuple[int, str]:
        for index, quote in enumerate(FUTURES_QUOTE_PRIORITY):
            if symbol.endswith(quote):
                return index, symbol
        return len(FUTURES_QUOTE_PRIORITY), symbol

    return sorted(symbols, key=sort_key)[0]


def fetch_spot_price(base_asset: str) -> tuple[str, Decimal] | None:
    for quote_asset in SPOT_QUOTE_PRIORITY:
        symbol = f"{base_asset}{quote_asset}"
        url = f"{SPOT_TICKER_API}?symbol={urllib.parse.quote(symbol)}"
        try:
            payload = fetch_json(url)
        except urllib.error.HTTPError as error:
            if error.code == 400:
                continue
            raise

        price = parse_decimal(payload.get("price"), default="")
        if price > 0:
            return symbol, price

    return None


def build_reference_price(
    base_asset: str,
    futures_symbols_by_base: dict[str, list[str]],
    futures_prices: dict[str, Decimal],
) -> tuple[str, Decimal] | None:
    spot_price = fetch_spot_price(base_asset)
    if spot_price is not None:
        return spot_price

    futures_symbols = futures_symbols_by_base.get(base_asset, [])
    if not futures_symbols:
        return None

    futures_symbol = pick_preferred_futures_symbol(futures_symbols)
    futures_price = futures_prices.get(futures_symbol)
    if futures_price is None or futures_price <= 0:
        return None

    return futures_symbol, futures_price


def resolve_symbol_collisions(
    candidates: list[dict[str, Any]],
    base_asset: str,
    futures_symbols_by_base: dict[str, list[str]],
    futures_prices: dict[str, Decimal],
) -> tuple[dict[str, Any], str, Decimal | None]:
    if len(candidates) == 1:
        futures_symbol = pick_preferred_futures_symbol(futures_symbols_by_base[base_asset])
        market_price = futures_prices.get(futures_symbol)
        return candidates[0], "unique_symbol", market_price

    reference = build_reference_price(base_asset, futures_symbols_by_base, futures_prices)
    if reference is None:
        best = max(candidates, key=lambda item: parse_decimal(item.get("fdv")))
        return best, "duplicate_symbol_fallback_fdv", None

    reference_symbol, reference_price = reference
    best = min(
        candidates,
        key=lambda item: abs(parse_decimal(item.get("price")) - reference_price),
    )
    match_method = f"duplicate_symbol_nearest_price:{reference_symbol}"
    return best, match_method, reference_price


def build_matches() -> list[TokenMatch]:
    bsc_tokens = load_bsc_tokens()
    futures_base_assets, futures_symbols_by_base = load_futures_symbols()
    futures_prices = load_futures_prices()

    tokens_by_symbol: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for token in bsc_tokens:
        symbol = str(token.get("symbol", "")).upper()
        if symbol:
            tokens_by_symbol[symbol].append(token)

    matches: list[TokenMatch] = []
    for base_asset in sorted(futures_base_assets):
        candidates = tokens_by_symbol.get(base_asset)
        if not candidates:
            continue

        token, match_method, market_price = resolve_symbol_collisions(
            candidates,
            base_asset,
            futures_symbols_by_base,
            futures_prices,
        )
        futures_symbol = pick_preferred_futures_symbol(futures_symbols_by_base[base_asset])
        token_price = parse_decimal(token.get("price"))
        price_gap_pct: Decimal | None = None
        if market_price and market_price > 0:
            price_gap_pct = abs(token_price - market_price) / market_price * Decimal("100")

        matches.append(
            TokenMatch(
                rank=0,
                symbol=base_asset,
                name=str(token.get("name", "")),
                futures_symbol=futures_symbol,
                fdv=parse_decimal(token.get("fdv")),
                token_price=token_price,
                market_price=market_price,
                price_gap_pct=price_gap_pct,
                contract_address=str(token.get("contractAddress", "")),
                match_method=match_method,
                token_decimals=int(token.get("decimals", 18)),
                holder_count=parse_optional_int(token.get("holders")),
                holding_amount=Decimal("0"),
            )
        )

    matches.sort(key=lambda item: item.fdv, reverse=True)

    ranked_matches: list[TokenMatch] = []
    for index, item in enumerate(matches, start=1):
        ranked_matches.append(
            TokenMatch(
                rank=index,
                symbol=item.symbol,
                name=item.name,
                futures_symbol=item.futures_symbol,
                fdv=item.fdv,
                token_price=item.token_price,
                market_price=item.market_price,
                price_gap_pct=item.price_gap_pct,
                contract_address=item.contract_address,
                match_method=item.match_method,
                token_decimals=item.token_decimals,
                holder_count=item.holder_count,
                holding_amount=item.holding_amount,
            )
        )

    return ranked_matches


def validate_wallet_address(wallet_address: str) -> str:
    normalized = wallet_address.strip()
    if not normalized.startswith("0x") or len(normalized) != 42:
        raise ValueError(f"Invalid wallet address: {wallet_address}")
    try:
        int(normalized[2:], 16)
    except ValueError as error:
        raise ValueError(f"Invalid wallet address: {wallet_address}") from error
    return normalized.lower()


def build_balance_of_call(wallet_address: str) -> str:
    return f"0x70a08231{wallet_address[2:].rjust(64, '0')}"


def parse_token_balance(hex_value: str, decimals: int) -> Decimal:
    raw_balance = int(hex_value, 16)
    if raw_balance == 0:
        return Decimal("0")
    return Decimal(raw_balance) / (Decimal(10) ** decimals)


def call_bsc_balance_of(contract_address: str, wallet_address: str) -> str:
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "eth_call",
        "params": [
            {
                "to": contract_address,
                "data": build_balance_of_call(wallet_address),
            },
            "latest",
        ],
    }

    for attempt in range(5):
        response = post_json(BSC_RPC_URL, payload)
        if isinstance(response, dict) and "result" in response:
            return str(response["result"])

        error = response.get("error", {}) if isinstance(response, dict) else {}
        message = str(error.get("message", "")).lower()
        code = error.get("code")
        if code == -32005 or "rate limit" in message:
            time.sleep(min(0.5 * (attempt + 1), 2.5))
            continue

        raise RuntimeError(f"Unexpected BSC RPC response for {contract_address}: {response}")

    raise RuntimeError(f"BSC RPC rate limit persisted for {contract_address}")


def fetch_wallet_balances(matches: list[TokenMatch], wallet_address: str) -> list[TokenMatch]:
    if not matches:
        return matches

    normalized_wallet = validate_wallet_address(wallet_address)
    balance_by_symbol: dict[str, Decimal] = {}
    valid_matches = [
        match
        for match in matches
        if match.contract_address.startswith("0x") and len(match.contract_address) == 42
    ]

    for index, match in enumerate(valid_matches, start=1):
        result = call_bsc_balance_of(match.contract_address, normalized_wallet)
        balance_by_symbol[match.symbol] = parse_token_balance(result, match.token_decimals)

        # Public BSC RPC endpoints batch-limit aggressively; a light pause keeps single-call mode stable.
        if index % 10 == 0:
            time.sleep(0.15)

    enriched_matches: list[TokenMatch] = []
    for match in matches:
        enriched_matches.append(
            TokenMatch(
                rank=match.rank,
                symbol=match.symbol,
                name=match.name,
                futures_symbol=match.futures_symbol,
                fdv=match.fdv,
                token_price=match.token_price,
                market_price=match.market_price,
                price_gap_pct=match.price_gap_pct,
                contract_address=match.contract_address,
                match_method=match.match_method,
                token_decimals=match.token_decimals,
                holder_count=match.holder_count,
                holding_amount=balance_by_symbol.get(match.symbol, Decimal("0")),
            )
        )

    return enriched_matches


def format_decimal(value: Decimal | None, places: int) -> str:
    if value is None:
        return "-"
    return f"{value:,.{places}f}"


def format_fdv_millions(value: Decimal) -> str:
    return f"{(value / MILLION):,.2f}M"


def calculate_holding_value(holding_amount: Decimal, token_price: Decimal) -> Decimal:
    required_precision = len(holding_amount.as_tuple().digits) + len(token_price.as_tuple().digits)
    with localcontext() as context:
        context.prec = max(required_precision, 50)
        return holding_amount * token_price


def format_token_amount(value: Decimal) -> str:
    if value == 0:
        return "0"
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def format_holder_count(value: int | None) -> str:
    return "-" if value is None else f"{value:,}"


def serialize_matches(matches: list[TokenMatch]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in matches:
        holding_value = calculate_holding_value(item.holding_amount, item.token_price)
        rows.append(
            {
                "rank": item.rank,
                "symbol": item.symbol,
                "name": item.name,
                "futures_symbol": item.futures_symbol,
                "fdv_usd": float(item.fdv),
                "fdv_m": float(item.fdv / MILLION),
                "token_price": format_token_amount(item.token_price),
                "market_price": (
                    format_token_amount(item.market_price) if item.market_price is not None else None
                ),
                "price_gap_pct": (
                    format_token_amount(item.price_gap_pct) if item.price_gap_pct is not None else None
                ),
                "contract_address": item.contract_address,
                "match_method": item.match_method,
                "holder_count": item.holder_count,
                "holding_amount": format_token_amount(item.holding_amount),
                "holding_value_usd": format_token_amount(holding_value),
                "has_holding": item.holding_amount > 0,
            }
        )
    return rows


def export_matches_to_excel(matches: list[TokenMatch], wallet_address: str) -> Path:
    from work.export_bsc_futures_excel import export_workbook

    payload = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "total_count": len(matches),
        "wallet_address": wallet_address,
        "held_token_count": sum(1 for match in matches if match.holding_amount > 0),
        "rows": serialize_matches(matches),
    }
    export_workbook(payload, XLSX_OUTPUT_PATH)

    return XLSX_OUTPUT_PATH


def print_table(matches: list[TokenMatch]) -> None:
    headers = (
        ("Symbol", 8),
        ("Futures", 14),
        ("FDV(M)", 14),
        ("TokenPrice", 14),
        ("MarketPrice", 14),
        ("Gap%", 10),
        ("Holders", 14),
        ("Holding", 20),
        ("HoldingValue(USD)", 20),
        ("Match", 36),
        ("Contract", 42),
    )
    header_line = " ".join(title.ljust(width) for title, width in headers)
    print(header_line)
    print("-" * len(header_line))

    for item in matches:
        row = (
            item.symbol.ljust(8),
            item.futures_symbol.ljust(14),
            format_fdv_millions(item.fdv).rjust(14),
            format_decimal(item.token_price, 8).rjust(14),
            format_decimal(item.market_price, 8).rjust(14),
            format_decimal(item.price_gap_pct, 4).rjust(10),
            format_holder_count(item.holder_count).rjust(14),
            format_token_amount(item.holding_amount).rjust(20),
            format_decimal(calculate_holding_value(item.holding_amount, item.token_price), 2).rjust(20),
            item.match_method[:36].ljust(36),
            item.contract_address.ljust(42),
        )
        print(" ".join(row))

    print()
    print(f"Total matched BSC tokens with Binance futures: {len(matches)}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wallet", help="Optional BSC wallet address for on-chain balances")
    args = parser.parse_args()
    wallet_address = args.wallet.strip() if args.wallet else ""

    try:
        matches = build_matches()
        if wallet_address:
            matches = fetch_wallet_balances(matches, wallet_address)
        xlsx_path = export_matches_to_excel(matches, wallet_address)
    except Exception as error:  # noqa: BLE001
        print(f"Failed to build token list: {error}", file=sys.stderr)
        return 1

    print_table(matches)
    print(f"Wallet: {wallet_address or 'Not configured'}")
    print(f"Excel output: {xlsx_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

import list_bsc_futures_by_fdv as source
from dashboard_server import (
    DashboardStore,
    aggregate_wallet_balances,
    build_dashboard,
    parse_settings_payload,
)


def match(
    symbol: str,
    contract_suffix: int,
    fdv: str,
    price: str,
    holding_amount: str = "0",
    holder_count: int | None = 100,
) -> source.TokenMatch:
    return source.TokenMatch(
        rank=1,
        symbol=symbol,
        name=f"{symbol} Token",
        futures_symbol=f"{symbol}USDT",
        fdv=Decimal(fdv),
        token_price=Decimal(price),
        market_price=Decimal(price),
        price_gap_pct=Decimal("0"),
        contract_address=f"0x{contract_suffix:040x}",
        match_method="unique_symbol",
        token_decimals=18,
        holder_count=holder_count,
        holding_amount=Decimal(holding_amount),
    )


class DashboardStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.store = DashboardStore(Path(self.temporary_directory.name) / "dashboard.sqlite3")
        self.alpha = match("ALPHA", 1, "10000000", "2", "5")
        self.empty = match("EMPTY", 2, "50000000", "1")
        self.pending = match("PENDING", 3, "25000000", "1")
        self.store.save_snapshot([self.alpha, self.empty, self.pending], wallet_count=1)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_dashboard_separates_unrecorded_pending_and_replenishment(self) -> None:
        self.store.save_manual_holding(
            {
                "source_name": "Binance",
                "asset_symbol": "ALPHA",
                "amount": "5",
                "contract_address": self.alpha.contract_address,
                "mapping_status": "confirmed",
            }
        )
        self.store.save_manual_holding(
            {
                "source_name": "Binance",
                "asset_symbol": "PENDING",
                "amount": "7",
                "mapping_status": "pending",
            }
        )

        dashboard = build_dashboard(self.store)
        tokens = {token["symbol"]: token for token in dashboard["tokens"]}

        self.assertEqual(tokens["ALPHA"]["holding_state"], "held")
        self.assertEqual(tokens["ALPHA"]["total_amount"], "10")
        self.assertEqual(tokens["ALPHA"]["total_value_usd"], 20.0)
        self.assertEqual(tokens["ALPHA"]["shortfall_usd"], 10.0)
        self.assertTrue(tokens["ALPHA"]["is_replenishment"])
        self.assertTrue(tokens["EMPTY"]["is_opportunity"])
        self.assertEqual(tokens["PENDING"]["holding_state"], "pending_confirmation")
        self.assertFalse(tokens["PENDING"]["is_opportunity"])
        self.assertFalse(tokens["PENDING"]["is_replenishment"])
        self.assertEqual(
            dashboard["manual_holding_candidates"]["2"],
            [self.pending.contract_address.lower()],
        )

    def test_csv_import_updates_the_same_manual_holding(self) -> None:
        csv_text = "\n".join(
            [
                "source_name,asset_symbol,amount,contract_address,mapping_status",
                f"Binance,ALPHA,5,{self.alpha.contract_address},confirmed",
            ]
        )
        self.assertEqual(self.store.import_manual_holdings(csv_text), 1)

        updated_csv = csv_text.replace(",5,", ",7,")
        self.assertEqual(self.store.import_manual_holdings(updated_csv), 1)
        holdings = self.store.manual_holdings()

        self.assertEqual(len(holdings), 1)
        self.assertEqual(holdings[0]["amount"], "7")

    def test_settings_validate_and_normalize_wallets(self) -> None:
        parsed = parse_settings_payload(
            {
                "wallet_addresses": (
                    "0x1111111111111111111111111111111111111111\n"
                    "0x1111111111111111111111111111111111111111"
                ),
                "default_initial_purchase_usd": "30",
                "default_target_value_usd": "45.5",
                "low_fdv_limit_usd": "200000000",
                "price_gap_alert_pct": "20",
            }
        )

        self.assertEqual(
            parsed["wallet_addresses"],
            "0x1111111111111111111111111111111111111111",
        )
        self.assertEqual(parsed["default_target_value_usd"], "45.5")

    def test_empty_wallet_settings_are_supported(self) -> None:
        parsed = parse_settings_payload({"wallet_addresses": ""})

        self.assertEqual(parsed["wallet_addresses"], "")

    def test_empty_wallet_list_keeps_holdings_at_zero(self) -> None:
        matches = aggregate_wallet_balances([self.alpha], [])

        self.assertEqual(matches[0].holding_amount, Decimal("0"))

    def test_empty_dashboard_keeps_manual_holdings_available_for_maintenance(self) -> None:
        empty_store = DashboardStore(Path(self.temporary_directory.name) / "empty.sqlite3")
        empty_store.save_manual_holding(
            {
                "source_name": "Binance",
                "asset_symbol": "UNMAPPED",
                "amount": "3",
                "mapping_status": "pending",
            }
        )

        dashboard = build_dashboard(empty_store)

        self.assertFalse(dashboard["has_snapshot"])
        self.assertEqual(dashboard["wallet_addresses"], [])
        self.assertEqual(dashboard["manual_holdings"][0]["asset_symbol"], "UNMAPPED")

    def test_holder_comparison_uses_two_dates_and_sorts_absolute_change(self) -> None:
        comparison_store = DashboardStore(
            Path(self.temporary_directory.name) / "comparison.sqlite3"
        )
        comparison_store.save_snapshot(
            [
                match("UP", 11, "10000000", "1", holder_count=100),
                match("DOWN", 12, "10000000", "1", holder_count=500),
                match("UNKNOWN", 13, "10000000", "1", holder_count=None),
            ],
            wallet_count=1,
            snapshot_date="2026-09-19",
        )
        comparison_store.save_snapshot(
            [
                match("UP", 11, "12000000", "2", holder_count=145),
                match("DOWN", 12, "9000000", "1", holder_count=490),
                match("UNKNOWN", 13, "10000000", "1", holder_count=None),
            ],
            wallet_count=1,
            snapshot_date="2026-09-20",
        )

        comparison = comparison_store.holder_comparison("2026-09-19", "2026-09-20")

        self.assertEqual(comparison["comparable_count"], 2)
        self.assertEqual(comparison["unavailable_count"], 1)
        self.assertEqual(comparison["rows"][0]["symbol"], "UP")
        self.assertEqual(comparison["rows"][0]["holder_change"], 45)
        self.assertEqual(comparison["rows"][1]["holder_change"], -10)
        self.assertEqual(comparison["rows"][0]["holder_change_pct"], 45.0)


if __name__ == "__main__":
    unittest.main()

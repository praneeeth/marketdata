"""Paper trading allocation and pool cash (India is the only market)."""

import math
import unittest
from types import SimpleNamespace

from src.modules.paper_trading.paper_trading_engine import (
    ALL_MARKETS,
    DEFAULT_ALLOCATIONS,
    allocations_from_excluded,
    compute_market_cash,
    market_allocations_or_default,
    normalize_allocations,
)


class TestNormalizeAllocations(unittest.TestCase):
    def test_india_is_the_only_market(self):
        """Allocation keys: India is the only market."""
        self.assertEqual(ALL_MARKETS, ("IN",))
        self.assertEqual(dict(DEFAULT_ALLOCATIONS), {"IN": 1.0})

    def test_fill_missing_market(self):
        self.assertEqual(normalize_allocations({}), {"IN": 0.0})
        self.assertAlmostEqual(normalize_allocations({"IN": 0.6})["IN"], 0.6)

    def test_legacy_markets_are_dropped(self):
        """Old CN/HK/US keys from before the India-only change are ignored."""
        self.assertEqual(normalize_allocations({"CN": 0.5, "IN": 0.4}), {"IN": 0.4})

    def test_clamps(self):
        self.assertEqual(normalize_allocations({"IN": -0.2}), {"IN": 0.0})
        self.assertEqual(normalize_allocations({"IN": 1.5}), {"IN": 1.0})
        self.assertEqual(normalize_allocations({"IN": "x"}), {"IN": 0.0})

    def test_none_input(self):
        self.assertEqual(normalize_allocations(None), {"IN": 0.0})


class TestAllocationsFromExcluded(unittest.TestCase):
    def test_nothing_excluded(self):
        self.assertEqual(allocations_from_excluded([]), {"IN": 1.0})
        self.assertEqual(allocations_from_excluded(None), {"IN": 1.0})

    def test_legacy_exclusions_do_not_matter(self):
        self.assertEqual(allocations_from_excluded(["US", "HK"]), {"IN": 1.0})

    def test_everything_excluded_falls_back_to_india(self):
        self.assertEqual(allocations_from_excluded(["IN"]), {"IN": 1.0})


class TestComputeMarketCash(unittest.TestCase):
    def test_basic(self):
        """子池现金 — 初始×比例 + 已实现 − 持仓成本"""
        # 100万×50% + 5000 − 300000 = 205000
        self.assertAlmostEqual(
            compute_market_cash(1_000_000, 0.5, 5000, 300000), 205000.0
        )

    def test_zero_ratio(self):
        """子池现金 — 比例 0 时初始资金为 0"""
        self.assertEqual(compute_market_cash(1_000_000, 0.0, 0, 0), 0.0)

    def test_over_allocated_negative(self):
        """子池现金 — 持仓超出额度时返回负（无新仓空间）"""
        # 100万×10% − 20万持仓 = -10万
        self.assertLess(compute_market_cash(1_000_000, 0.1, 0, 200000), 0)


class TestMarketAllocationsOrDefault(unittest.TestCase):
    def test_empty_falls_back_default(self):
        """账户比例 — 未配置时回落默认配置"""
        acc = SimpleNamespace(market_allocations=None, initial_capital=1_000_000)
        out = market_allocations_or_default(acc)
        self.assertEqual(out, dict(DEFAULT_ALLOCATIONS))

    def test_configured_is_normalized(self):
        """账户比例 — 已配置则归一化返回"""
        acc = SimpleNamespace(
            market_allocations={"IN": 0.8}, initial_capital=1_000_000
        )
        out = market_allocations_or_default(acc)
        self.assertEqual(out, {"IN": 0.8})

    def test_sum_le_one_invariant(self):
        """账户比例 — 合理配置合计不超过 1"""
        acc = SimpleNamespace(
            market_allocations={"IN": 1.0}, initial_capital=1_000_000
        )
        out = market_allocations_or_default(acc)
        self.assertTrue(math.isclose(sum(out.values()), 1.0, abs_tol=1e-6))


if __name__ == "__main__":
    unittest.main()

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
        """Sub-pool cash: initial x ratio + realised - position cost."""
        # 1,000,000 x 50% + 5000 - 300000 = 205000
        self.assertAlmostEqual(
            compute_market_cash(1_000_000, 0.5, 5000, 300000), 205000.0
        )

    def test_zero_ratio(self):
        """Sub-pool cash: a ratio of 0 gives initial capital 0."""
        self.assertEqual(compute_market_cash(1_000_000, 0.0, 0, 0), 0.0)

    def test_over_allocated_negative(self):
        """Sub-pool cash: negative when positions exceed the allocation (no room for new positions)."""
        # 1,000,000 x 10% - 200,000 in positions = -100,000
        self.assertLess(compute_market_cash(1_000_000, 0.1, 0, 200000), 0)


class TestMarketAllocationsOrDefault(unittest.TestCase):
    def test_empty_falls_back_default(self):
        """Account ratios: fall back to the default allocation when unset."""
        acc = SimpleNamespace(market_allocations=None, initial_capital=1_000_000)
        out = market_allocations_or_default(acc)
        self.assertEqual(out, dict(DEFAULT_ALLOCATIONS))

    def test_configured_is_normalized(self):
        """Account ratios: normalised when configured."""
        acc = SimpleNamespace(
            market_allocations={"IN": 0.8}, initial_capital=1_000_000
        )
        out = market_allocations_or_default(acc)
        self.assertEqual(out, {"IN": 0.8})

    def test_sum_le_one_invariant(self):
        """Account ratios: a sensible allocation adds up to no more than 1."""
        acc = SimpleNamespace(
            market_allocations={"IN": 1.0}, initial_capital=1_000_000
        )
        out = market_allocations_or_default(acc)
        self.assertTrue(math.isclose(sum(out.values()), 1.0, abs_tol=1e-6))


if __name__ == "__main__":
    unittest.main()

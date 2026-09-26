"""Unit tests for factor evaluation correlations (Phase 2); pure functions, no DB."""

from src.modules.strategy.factor_eval import pearson, spearman


def test_pearson_perfect_positive():
    """Perfect positive linear correlation = +1."""
    assert abs(pearson([1, 2, 3, 4, 5], [2, 4, 6, 8, 10]) - 1.0) < 1e-9


def test_pearson_perfect_negative():
    """Perfect negative linear correlation = -1."""
    assert abs(pearson([1, 2, 3, 4, 5], [10, 8, 6, 4, 2]) + 1.0) < 1e-9


def test_pearson_too_few_returns_none():
    """Fewer than 3 samples returns None."""
    assert pearson([1, 2], [1, 2]) is None


def test_pearson_zero_variance_none():
    """Zero variance (a constant series) returns None."""
    assert pearson([1, 1, 1, 1], [1, 2, 3, 4]) is None


def test_spearman_monotonic_nonlinear():
    """A monotonic non-linear relationship gives Spearman = +1 (rank correlation)."""
    assert abs(spearman([1, 2, 3, 4, 5], [1, 4, 9, 16, 25]) - 1.0) < 1e-9


def test_spearman_handles_ties():
    """Ties still give a valid correlation (average ranks)."""
    r = spearman([1, 1, 2, 3], [1, 2, 2, 3])
    assert r is not None and -1.0 <= r <= 1.0

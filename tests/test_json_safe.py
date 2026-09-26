"""tests for src/core/json_safe.py"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from enum import Enum

from src.platform.persistence.json_safe import to_jsonable


class TestPrimitives:
    def test_none(self):
        """Basic types: None is returned as is."""
        assert to_jsonable(None) is None

    def test_str(self):
        """Basic types: a string is returned as is."""
        assert to_jsonable("hello") == "hello"

    def test_int(self):
        """Basic types: an integer is returned as is."""
        assert to_jsonable(42) == 42

    def test_float(self):
        """Basic types: a float is returned as is."""
        assert to_jsonable(3.14) == 3.14

    def test_bool(self):
        """Basic types: a boolean is returned as is."""
        assert to_jsonable(True) is True


class TestDatetime:
    def test_datetime(self):
        """Date/time: datetime becomes ISO format."""
        dt = datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
        assert to_jsonable(dt) == "2024-01-15T10:30:00+00:00"

    def test_date(self):
        """Date/time: date becomes ISO format."""
        d = date(2024, 1, 15)
        assert to_jsonable(d) == "2024-01-15"


class TestEnum:
    def test_enum_value(self):
        """Enum: a string enum gives its value."""
        class Color(Enum):
            RED = "red"
            BLUE = "blue"

        assert to_jsonable(Color.RED) == "red"

    def test_enum_int_value(self):
        """Enum: an integer enum gives its value."""
        class Priority(Enum):
            HIGH = 1
            LOW = 2

        assert to_jsonable(Priority.HIGH) == 1


class TestCollections:
    def test_dict(self):
        """Collections: a dict is converted recursively."""
        assert to_jsonable({"a": 1, "b": "x"}) == {"a": 1, "b": "x"}

    def test_list(self):
        """Collections: a list is converted recursively."""
        assert to_jsonable([1, "a", None]) == [1, "a", None]

    def test_set(self):
        """Collections: a set becomes a list."""
        result = to_jsonable({1})
        assert isinstance(result, list)
        assert result == [1]

    def test_nested(self):
        """Collections: nested structures are converted recursively."""
        data = {"items": [{"name": "test", "date": date(2024, 1, 1)}]}
        result = to_jsonable(data)
        assert result == {"items": [{"name": "test", "date": "2024-01-01"}]}


class TestDataclass:
    def test_dataclass(self):
        """dataclass: becomes a dict."""
        @dataclass
        class Point:
            x: int
            y: int

        assert to_jsonable(Point(1, 2)) == {"x": 1, "y": 2}


class TestCircularReference:
    def test_circular_dict(self):
        """Circular reference: detected; returns <circular>."""
        d: dict = {}
        d["self"] = d
        result = to_jsonable(d)
        assert result["self"] == "<circular>"

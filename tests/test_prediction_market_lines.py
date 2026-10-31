from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

import numpy as np

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "predictions" / "train-models.py"
SPEC = importlib.util.spec_from_file_location("train_models", SCRIPT)
train_models = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(train_models)


class MarketSpecificLineTests(unittest.TestCase):
    def setUp(self):
        self.actual = np.arange(7, dtype=float)
        self.mean = np.full(7, 3.0)

    def test_corner_market_uses_corner_lines_by_default(self):
        result = train_models.evaluate_lines(self.actual, self.mean)
        self.assertEqual(
            [item["line"] for item in result],
            [7.5, 8.5, 9.5, 10.5, 11.5, 12.5],
        )

    def test_yellow_card_market_uses_card_lines(self):
        result = train_models.evaluate_lines(
            self.actual,
            self.mean,
            lines=train_models.YELLOW_CARD_LINES,
        )
        self.assertEqual(
            [item["line"] for item in result],
            [0.5, 1.5, 2.5, 3.5, 4.5, 5.5],
        )
        self.assertEqual(result[0]["over"]["eventCount"], 6)
        self.assertEqual(result[-1]["over"]["eventCount"], 1)

    def test_negative_binomial_uses_card_lines(self):
        result = train_models.evaluate_lines(
            self.actual,
            self.mean,
            distribution="negative_binomial",
            alpha=0.2,
            lines=train_models.YELLOW_CARD_LINES,
        )
        self.assertEqual(
            [item["line"] for item in result],
            train_models.YELLOW_CARD_LINES,
        )


if __name__ == "__main__":
    unittest.main()

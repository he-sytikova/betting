import importlib.util
from pathlib import Path
import unittest

import numpy as np

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "predictions" / "train-goals-markets.py"
SPEC = importlib.util.spec_from_file_location("train_goals_markets", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class GoalModelBaselineTests(unittest.TestCase):
    def test_metrics_compare_against_training_mean(self):
        actual = np.asarray([0, 1, 2, 3, 1, 2], dtype=float)
        predictions = np.asarray([0.2, 1.1, 1.8, 2.7, 1.2, 2.1], dtype=float)
        result = MODULE.metrics(actual, predictions, baseline_mean=1.5)

        self.assertEqual(result["rows"], 6)
        self.assertEqual(result["baselineMean"], 1.5)
        self.assertLess(result["mae"], result["baselineMae"])
        self.assertLess(result["poissonDeviance"], result["baselinePoissonDeviance"])
        self.assertGreater(result["maeImprovementPct"], 0)
        self.assertGreater(result["devianceImprovementPct"], 0)
        self.assertIn("training partition", result["baselineReference"])

    def test_baseline_is_not_derived_from_holdout_actual_mean(self):
        actual = np.asarray([0, 0, 10, 10], dtype=float)
        predictions = np.asarray([1, 1, 1, 1], dtype=float)
        result = MODULE.metrics(actual, predictions, baseline_mean=2.0)

        self.assertEqual(result["actualMean"], 5.0)
        self.assertEqual(result["baselineMean"], 2.0)


if __name__ == "__main__":
    unittest.main()

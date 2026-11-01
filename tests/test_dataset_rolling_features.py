from __future__ import annotations

import importlib.util
import unittest
from collections import deque
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "predictions" / "build-dataset.py"
SPEC = importlib.util.spec_from_file_location("build_dataset", SCRIPT)
build_dataset = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(build_dataset)


class RollingFeatureTests(unittest.TestCase):
    def test_rolling_features_are_calculated_from_prior_history(self):
        history = deque([(2.0, 4.0), (6.0, 8.0)], maxlen=10)
        result = build_dataset.team_features(history, "foul_team")
        self.assertEqual(result["foul_team_for_avg5"], 4.0)
        self.assertEqual(result["foul_team_against_avg5"], 6.0)
        self.assertEqual(result["foul_team_for_avg10"], 4.0)
        self.assertEqual(result["foul_team_against_avg10"], 6.0)
        self.assertEqual(result["foul_team_n5"], 2)
        self.assertEqual(result["foul_team_n10"], 2)

    def test_empty_history_is_explicitly_missing(self):
        result = build_dataset.team_features(deque(), "shots_team")
        self.assertIsNone(result["shots_team_for_avg5"])
        self.assertIsNone(result["shots_team_against_avg10"])
        self.assertEqual(result["shots_team_n5"], 0)


if __name__ == "__main__":
    unittest.main()

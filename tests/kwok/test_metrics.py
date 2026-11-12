"""Verify CPU usage aggregation."""

import unittest

from run_load import summarize_cpu


class CpuSummaryTests(unittest.TestCase):
    def test_weighted_average_includes_short_final_interval(self) -> None:
        samples = [
            {"interval_seconds": 2.0, "cpu_percent": 100.0, "peak_eligible": True},
            {"interval_seconds": 1.0, "cpu_percent": 25.0, "peak_eligible": True},
            {"interval_seconds": 0.1, "cpu_percent": 300.0, "peak_eligible": False},
        ]
        result = summarize_cpu(samples)
        self.assertAlmostEqual(result["average_cpu_percent"], 255 / 3.1)
        self.assertEqual(result["peak_cpu_percent"], 100)

    def test_empty_samples_are_an_error(self) -> None:
        with self.assertRaisesRegex(ValueError, "no operator CPU samples"):
            summarize_cpu([])


if __name__ == "__main__":
    unittest.main()

"""Unit checks for the load harness's hold-time option."""

import argparse
import asyncio
import sys
import unittest
from typing import Any
from unittest.mock import patch

import run_load


class HoldTimeTests(unittest.TestCase):
    def parse(self, *args: str) -> argparse.Namespace:
        with patch.object(sys, "argv", ["run_load.py", *args]):
            return run_load.arguments()

    def test_default_and_legacy_seconds(self) -> None:
        self.assertEqual(self.parse().hold_ms, 2000)
        self.assertEqual(self.parse("--hold-seconds", "0.5").hold_ms, 500)

    def test_milliseconds_and_zero(self) -> None:
        self.assertEqual(self.parse("--hold-ms", "1.5").hold_ms, 1.5)
        self.assertEqual(self.parse("--hold-ms", "0").hold_ms, 0)

    def test_invalid_milliseconds(self) -> None:
        for value in ("-1", "inf", "nan"):
            with self.subTest(value=value), self.assertRaises(SystemExit), patch("sys.stderr"):
                self.parse("--hold-ms", value)


class HoldDurationTests(unittest.IsolatedAsyncioTestCase):
    async def test_milliseconds_converted_for_async_sleep(self) -> None:
        class FakeAPI(run_load.APIClient):
            def __init__(self) -> None:
                pass

            async def request(
                self, method: str, path: str, *, body: dict[str, Any] | None = None, expected: tuple[int, ...] = (200, 201)
            ) -> dict[str, int]:
                return {"code": 404} if method == "GET" else {}

        sleeps: list[float] = []
        original_sleep = asyncio.sleep

        async def record_sleep(delay: float) -> None:
            sleeps.append(delay)
            await original_sleep(delay)

        args = argparse.Namespace(max_crs=1, namespaces=1, concurrency=1, duration=0.005, hold_ms=1.5)
        with patch.object(run_load.asyncio, "sleep", record_sleep):
            await run_load.run_load(FakeAPI(), args)
        self.assertTrue(sleeps)
        self.assertTrue(all(delay == 0.0015 for delay in sleeps))


if __name__ == "__main__":
    unittest.main()

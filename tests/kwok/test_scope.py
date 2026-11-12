"""Verify scope selection and controller startup checks."""

import asyncio
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch

import run_load


class ScopeConfigTests(unittest.TestCase):
    def test_scope_arguments(self) -> None:
        with patch.object(sys, "argv", ["run_load.py"]):
            self.assertEqual(run_load.arguments().scope, "cluster")
        with patch.object(sys, "argv", ["run_load.py", "--scope", "namespace"]):
            self.assertEqual(run_load.arguments().scope, "namespace")

    def test_config(self) -> None:
        cluster = run_load.operator_config("cluster", 3)
        self.assertTrue(cluster["cluster_scope"])
        self.assertEqual(cluster["watched_namespaces"], [])
        namespace = run_load.operator_config("namespace", 3)
        self.assertFalse(namespace["cluster_scope"])
        self.assertEqual(namespace["watched_namespaces"], ["operator", "load-000000", "load-000001", "load-000002"])


class ControllerStartupTests(unittest.IsolatedAsyncioTestCase):
    async def test_waits_for_each_namespace_controller(self) -> None:
        process = await asyncio.create_subprocess_exec(sys.executable, "-c", "import time; time.sleep(10)")
        try:
            with tempfile.TemporaryDirectory() as directory:
                log = pathlib.Path(directory) / "operator.log"
                log.write_text("Operator has become the leader\nStarting controller for namespace: operator\n")
                task = asyncio.create_task(run_load.wait_for_controllers(process, log, "namespace", 2))
                await asyncio.sleep(0.25)
                self.assertFalse(task.done())
                with log.open("a") as output:
                    output.write(
                        "Starting controller for namespace: load-000000\nStarting controller for namespace: load-000001\n"
                    )
                await asyncio.wait_for(task, 2)
        finally:
            process.terminate()
            await process.wait()

    async def test_cluster_controller(self) -> None:
        process = await asyncio.create_subprocess_exec(sys.executable, "-c", "import time; time.sleep(10)")
        try:
            with tempfile.TemporaryDirectory() as directory:
                log = pathlib.Path(directory) / "operator.log"
                log.write_text("Operator has become the leader\nStarting controller for cluster-scoped ImageSyncs\n")
                await asyncio.wait_for(run_load.wait_for_controllers(process, log, "cluster", 2), 2)
        finally:
            process.terminate()
            await process.wait()


if __name__ == "__main__":
    unittest.main()

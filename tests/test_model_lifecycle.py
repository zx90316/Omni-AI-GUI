"""Fast tests for non-ASR model keep-alive behavior."""

import threading
import time
import unittest

from backend.model_lifecycle import ModelIdleManager


class ModelIdleManagerTests(unittest.TestCase):
    def test_loaded_resource_unloads_after_idle_timeout(self):
        unloaded = threading.Event()
        manager = ModelIdleManager(timeout_seconds=0.04)
        manager.register("model", unloaded.set)
        manager.mark_loaded("model")

        self.assertTrue(unloaded.wait(0.5))
        self.assertFalse(manager.status()["model"]["loaded"])

    def test_active_inference_prevents_unload_until_it_finishes(self):
        unloaded = threading.Event()
        manager = ModelIdleManager(timeout_seconds=0.04)
        manager.register("model", unloaded.set)

        with manager.activity("model"):
            manager.mark_loaded("model")
            time.sleep(0.08)
            self.assertFalse(unloaded.is_set())
            self.assertEqual(manager.status()["model"]["active"], 1)

        self.assertTrue(unloaded.wait(0.5))

    def test_touch_restarts_idle_deadline(self):
        unloaded = threading.Event()
        manager = ModelIdleManager(timeout_seconds=0.08)
        manager.register("model", unloaded.set)
        manager.mark_loaded("model")
        time.sleep(0.05)
        manager.touch("model")
        time.sleep(0.05)

        self.assertFalse(unloaded.is_set())
        self.assertTrue(unloaded.wait(0.5))

    def test_unload_callback_can_reenter_other_locks(self):
        external_lock = threading.Lock()
        unloaded = threading.Event()

        def unload():
            with external_lock:
                unloaded.set()

        manager = ModelIdleManager(timeout_seconds=0.02)
        manager.register("model", unload)
        with external_lock:
            manager.mark_loaded("model")
            time.sleep(0.04)
            self.assertFalse(unloaded.is_set())

        self.assertTrue(unloaded.wait(0.5))

    def test_resources_keep_independent_idle_deadlines(self):
        reranker_unloaded = threading.Event()
        embedding_unloaded = threading.Event()
        manager = ModelIdleManager(timeout_seconds=0.08)
        manager.register("reranker", reranker_unloaded.set)
        manager.register("embedding", embedding_unloaded.set)
        manager.mark_loaded("reranker")
        manager.mark_loaded("embedding")

        time.sleep(0.05)
        with manager.activity("embedding"):
            pass

        self.assertTrue(reranker_unloaded.wait(0.2))
        self.assertFalse(embedding_unloaded.is_set())
        self.assertTrue(embedding_unloaded.wait(0.5))


if __name__ == "__main__":
    unittest.main()

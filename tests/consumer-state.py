#!/usr/bin/env python3
"""Regression tests for Consumer state transactions and callback accounting."""

import importlib.util
import json
import multiprocessing
import tempfile
import threading
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "nwdaf_consumer", ROOT / "tools" / "nwdaf-consumer" / "consumer.py"
)
CONSUMER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CONSUMER)


def subscription(path, correlation, location=None):
    return {
        "path": path,
        "tac": "000001" if path == "a" else "000002",
        "nfInstanceId": path * 8,
        "apiRoot": "http://192.0.2.{}/nnwdaf-eventssubscription/v1".format(1 if path == "a" else 2),
        "correlationId": correlation,
        "location": location or "http://192.0.2.{}/subscriptions/{}".format(1 if path == "a" else 2, path),
        "status": "active",
    }


def callback_worker(state_path, correlation, count):
    store = CONSUMER.StateStore(state_path)
    for _ in range(count):
        store.record_notification({"notifCorrId": correlation})


def main():
    with tempfile.TemporaryDirectory() as temporary:
        state_path = Path(temporary) / "subscriptions.json"
        store = CONSUMER.StateStore(state_path)
        store.write({
            "status": "active",
            "subscriptions": [subscription("a", "corr-a"), subscription("b", "corr-b")],
            "notificationCount": 0,
        })

        threads = []
        for correlation in ("corr-a", "corr-b"):
            for _ in range(2):
                thread = threading.Thread(target=callback_worker, args=(state_path, correlation, 10))
                thread.start()
                threads.append(thread)
        for thread in threads:
            thread.join()

        context = multiprocessing.get_context("fork")
        processes = []
        for correlation in ("corr-a", "corr-b"):
            for _ in range(2):
                process = context.Process(target=callback_worker, args=(state_path, correlation, 10))
                process.start()
                processes.append(process)
        for process in processes:
            process.join()
            assert process.exitcode == 0, process.exitcode

        state = store.read()
        assert state["notificationCount"] == 80, state
        assert state["callbacksByPath"]["a"]["requestCount"] == 40, state
        assert state["callbacksByPath"]["b"]["requestCount"] == 40, state
        json.loads(state_path.read_text(encoding="utf-8"))

        store.record_notification({"notifCorrId": "not-owned"})
        state = store.read()
        assert state["unknownCallbacks"]["requestCount"] == 1, state
        assert state["unknownCallbacks"]["correlationIds"] == ["not-owned"], state

        legacy_path = Path(temporary) / "legacy.json"
        legacy = CONSUMER.StateStore(legacy_path)
        legacy.write({
            "status": "active",
            "subscriptions": [subscription("a", "legacy-a")],
            "notificationCount": 7,
        })
        legacy.record_notification({"notifCorrId": "legacy-a"})
        state = legacy.read()
        assert state["notificationCount"] == 8, state
        assert state["callbacksByPath"]["a"]["requestCount"] == 1, state

        early_path = Path(temporary) / "early.json"
        early = CONSUMER.StateStore(early_path)
        original_discover = CONSUMER.discover
        original_create_one = CONSUMER.create_one
        try:
            candidates = [
                {"path": "a", "tac": "000001", "nfInstanceId": "a" * 8, "apiRoot": "http://192.0.2.1"},
                {"path": "b", "tac": "000002", "nfInstanceId": "b" * 8, "apiRoot": "http://192.0.2.2"},
            ]
            CONSUMER.discover = lambda _config: candidates

            def create_one(_config, candidate):
                result = subscription(candidate["path"], candidate["correlationId"])
                if candidate["path"] == "a":
                    early.record_notification({"notifCorrId": candidate["correlationId"]})
                return result

            CONSUMER.create_one = create_one
            state = CONSUMER.create_all({}, early)
        finally:
            CONSUMER.discover = original_discover
            CONSUMER.create_one = original_create_one
        assert state["notificationCount"] == 1, state
        assert state["callbacksByPath"]["a"]["requestCount"] == 1, state
        assert "unknownCallbacks" not in state, state
        assert len(state["subscriptions"]) == 2, state

        rerun_path = Path(temporary) / "rerun.json"
        rerun = CONSUMER.StateStore(rerun_path)
        rerun.write({
            "status": "stopped",
            "subscriptions": [subscription("a", "old-a"), subscription("b", "old-b")],
            "notificationCount": 88,
            "lastNotificationAt": "2026-08-13T00:00:00Z",
            "callbacksByPath": {
                "a": {"correlationId": "old-a", "requestCount": 44},
                "b": {"correlationId": "old-b", "requestCount": 44},
            },
            "unknownCallbacks": {"requestCount": 2, "correlationIds": ["old-unknown"]},
        })
        original_discover = CONSUMER.discover
        original_create_one = CONSUMER.create_one
        try:
            CONSUMER.discover = lambda _config: [
                {"path": "a", "tac": "000001", "nfInstanceId": "a" * 8, "apiRoot": "http://192.0.2.1"},
                {"path": "b", "tac": "000002", "nfInstanceId": "b" * 8, "apiRoot": "http://192.0.2.2"},
            ]
            CONSUMER.create_one = lambda _config, candidate: subscription(
                candidate["path"], candidate["correlationId"]
            )
            state = CONSUMER.create_all({}, rerun)
        finally:
            CONSUMER.discover = original_discover
            CONSUMER.create_one = original_create_one
        assert state["notificationCount"] == 0, state
        assert state["callbacksByPath"]["a"]["requestCount"] == 0, state
        assert state["callbacksByPath"]["b"]["requestCount"] == 0, state
        assert "lastNotificationAt" not in state, state
        assert "unknownCallbacks" not in state, state
        rerun.record_notification({"notifCorrId": state["subscriptions"][0]["correlationId"]})
        state = rerun.read()
        assert state["notificationCount"] == 1, state
        assert state["callbacksByPath"]["a"]["requestCount"] == 1, state

        delete_path = Path(temporary) / "delete.json"
        deleting = CONSUMER.StateStore(delete_path)
        deleting.write({
            "status": "active",
            "subscriptions": [subscription("a", "delete-a")],
            "notificationCount": 0,
        })
        original_delete = CONSUMER.delete_location
        try:
            CONSUMER.delete_location = lambda _location: deleting.record_notification({"notifCorrId": "delete-a"})
            CONSUMER.delete_all(deleting)
        finally:
            CONSUMER.delete_location = original_delete
        state = deleting.read()
        assert state["status"] == "stopped", state
        assert state["subscriptions"][0]["status"] == "deleted", state
        assert state["notificationCount"] == 1, state
        assert state["callbacksByPath"]["a"]["requestCount"] == 1, state

    print("CONSUMER_STATE_TEST status=passed callbacks=80")


if __name__ == "__main__":
    main()

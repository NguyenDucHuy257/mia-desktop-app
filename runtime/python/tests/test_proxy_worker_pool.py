import base64
import hashlib
import os
import tempfile
import threading
import time
import unittest
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mia_backend import ProductionBackend
import mia_runtime
from app.job_engine.models import CreateJobRequest, JobStageSpec
from mia_local_job_repository import LocalSequentialJobRepository
from mia_local_worker import LocalWorkerLoop
from app.worker_runtime.handler import InvoiceCrawlTaskHandler
from app.session_manager.models import AuthenticationFailedError


class ProxyWorkerPoolTests(unittest.TestCase):
    def setUp(self):
        self.old_key = os.environ.get("MIA_SESSION_ENCRYPTION_KEY")
        self.old_key_id = os.environ.get("MIA_SESSION_ENCRYPTION_KEY_ID")
        os.environ["MIA_SESSION_ENCRYPTION_KEY"] = base64.urlsafe_b64encode(b"p" * 32).decode()
        os.environ["MIA_SESSION_ENCRYPTION_KEY_ID"] = "proxy-pool-test"

    def tearDown(self):
        for name, value in (
            ("MIA_SESSION_ENCRYPTION_KEY", self.old_key),
            ("MIA_SESSION_ENCRYPTION_KEY_ID", self.old_key_id),
        ):
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    def test_no_proxy_keeps_exactly_one_direct_worker(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = ProductionBackend(Path(directory), start_worker=False)
            try:
                result = backend.configure_proxies([])
                self.assertEqual(result["worker_count"], 1)
                self.assertEqual(len(backend._worker_slots), 1)
                self.assertIsNone(backend._worker_slots[0]["route"])
            finally:
                backend.close()

    def test_direct_slot_reuses_cached_token_but_proxy_slot_forces_route_auth(self):
        job = SimpleNamespace(job_id="job-route", parameters={"session_hash": "a" * 64})
        direct = object.__new__(InvoiceCrawlTaskHandler)
        direct.session_manager = unittest.mock.Mock()
        direct.worker_id = "slot-direct"
        direct.fixed_runtime_route = None
        InvoiceCrawlTaskHandler.authenticate_job.__wrapped__(direct, job)
        self.assertFalse(direct.session_manager.authenticate_session_hash.call_args.kwargs["force"])

        proxy = object.__new__(InvoiceCrawlTaskHandler)
        proxy.session_manager = unittest.mock.Mock()
        proxy.worker_id = "slot-proxy"
        proxy.fixed_runtime_route = "http://proxy.invalid:1001"
        InvoiceCrawlTaskHandler.authenticate_job.__wrapped__(proxy, job)
        self.assertTrue(proxy.session_manager.authenticate_session_hash.call_args.kwargs["force"])

    def test_temporary_login_rejection_is_retried_automatically(self):
        job = SimpleNamespace(job_id="job-retry", parameters={"session_hash": "a" * 64})
        handler = object.__new__(InvoiceCrawlTaskHandler)
        handler.session_manager = unittest.mock.Mock()
        handler.session_manager.authenticate_session_hash.side_effect = [
            AuthenticationFailedError("source_login_rejected"),
            SimpleNamespace(),
        ]
        handler.worker_id = "slot-direct"
        handler.fixed_runtime_route = None
        handler._shutdown_requested = threading.Event()
        events = []
        with patch(
            "app.worker_runtime.handler.AUTH_RETRY_DELAYS_SECONDS", (0, 0)
        ):
            InvoiceCrawlTaskHandler.authenticate_job.__wrapped__(handler, job, events.append)
        self.assertEqual(handler.session_manager.authenticate_session_hash.call_count, 2)
        self.assertIn("auth_retry_wait", events)

    def test_invalid_credentials_are_not_retried(self):
        job = SimpleNamespace(job_id="job-no-retry", parameters={"session_hash": "a" * 64})
        handler = object.__new__(InvoiceCrawlTaskHandler)
        handler.session_manager = unittest.mock.Mock()
        handler.session_manager.authenticate_session_hash.side_effect = (
            AuthenticationFailedError("invalid_source_credentials")
        )
        handler.worker_id = "slot-direct"
        handler.fixed_runtime_route = None
        handler._shutdown_requested = threading.Event()
        with self.assertRaises(AuthenticationFailedError):
            InvoiceCrawlTaskHandler.authenticate_job.__wrapped__(handler, job)
        self.assertEqual(handler.session_manager.authenticate_session_hash.call_count, 1)

    def test_two_live_proxies_create_three_isolated_routes(self):
        routes = ("http://proxy-one.invalid:1001", "http://proxy-two.invalid:1002")
        with tempfile.TemporaryDirectory() as directory:
            backend = ProductionBackend(Path(directory), start_worker=False)
            try:
                with patch.object(backend, "_probe_proxy", return_value=True):
                    result = backend.configure_proxies(list(routes))
                self.assertEqual(result["live_count"], 2)
                self.assertEqual(result["worker_count"], 3)
                self.assertEqual(
                    [slot["route"] for slot in backend._worker_slots],
                    [None, *routes],
                )
                self.assertEqual(len({slot["worker_id"] for slot in backend._worker_slots}), 3)
            finally:
                backend.close()

    def test_dead_proxy_is_excluded_without_disabling_direct_worker(self):
        routes = ("http://live.invalid:1001", "http://dead.invalid:1002")
        with tempfile.TemporaryDirectory() as directory:
            backend = ProductionBackend(Path(directory), start_worker=False)
            try:
                with patch.object(backend, "_probe_proxy", side_effect=[True, False]):
                    result = backend.configure_proxies(list(routes))
                self.assertEqual(result["failed_count"], 1)
                self.assertEqual(result["worker_count"], 2)
            finally:
                backend.close()

    def test_three_non_serialized_loops_can_enter_source_work_together(self):
        barrier = threading.Barrier(3)
        entered = []
        lock = threading.Lock()

        class Supervisor:
            repository = SimpleNamespace(recover_expired_leases=lambda: None)

            def __init__(self, worker_id):
                self.worker_id = worker_id

            def set_stop_event(self, _event):
                pass

            def run_once(self):
                barrier.wait(timeout=2)
                with lock:
                    entered.append(self.worker_id)
                return SimpleNamespace(job=None)

        threads = []
        for index in range(3):
            loop = LocalWorkerLoop(
                Supervisor(f"slot-{index}"), idle_backoff_seconds=0.001,
                error_backoff_seconds=0.001, orphan_scan_seconds=30,
                serialize_source=False,
            )
            threads.append(threading.Thread(
                target=loop.run, args=(threading.Event(),),
                kwargs={"max_iterations": 1},
            ))
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=3)
        self.assertEqual(sorted(entered), ["slot-0", "slot-1", "slot-2"])
        self.assertFalse(any(thread.is_alive() for thread in threads))

    def test_three_slots_never_run_a_fourth_account_and_reuse_free_slots(self):
        queue = list(range(5))
        queue_lock = threading.Lock()
        activity_lock = threading.Lock()
        active = 0
        peak = 0
        completed = []
        per_slot = {}

        class Supervisor:
            repository = SimpleNamespace(recover_expired_leases=lambda: None)

            def __init__(self, worker_id):
                self.worker_id = worker_id

            def set_stop_event(self, _event):
                pass

            def run_once(self):
                nonlocal active, peak
                with queue_lock:
                    task = queue.pop(0) if queue else None
                if task is None:
                    return SimpleNamespace(job=None)
                with activity_lock:
                    active += 1
                    peak = max(peak, active)
                time.sleep(0.02)
                with activity_lock:
                    active -= 1
                    completed.append(task)
                    per_slot[self.worker_id] = per_slot.get(self.worker_id, 0) + 1
                return SimpleNamespace(job=task)

        threads = []
        for index in range(3):
            loop = LocalWorkerLoop(
                Supervisor(f"slot-{index}"), idle_backoff_seconds=0.001,
                error_backoff_seconds=0.001, orphan_scan_seconds=30,
                serialize_source=False,
            )
            threads.append(threading.Thread(
                target=loop.run, args=(threading.Event(),),
                kwargs={"max_iterations": 4},
            ))
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=3)
        self.assertEqual(sorted(completed), list(range(5)))
        self.assertEqual(peak, 3)
        self.assertTrue(any(count >= 2 for count in per_slot.values()))

    def test_failure_in_one_loop_does_not_stop_another_slot(self):
        completed = threading.Event()

        class FailingSupervisor:
            worker_id = "failed-slot"
            repository = SimpleNamespace(recover_expired_leases=lambda: None)
            set_stop_event = lambda self, event: None

            def run_once(self):
                raise RuntimeError("synthetic failure")

        class HealthySupervisor(FailingSupervisor):
            worker_id = "healthy-slot"

            def run_once(self):
                completed.set()
                return SimpleNamespace(job=None)

        loops = [
            LocalWorkerLoop(
                supervisor, idle_backoff_seconds=0.001,
                error_backoff_seconds=0.001, orphan_scan_seconds=30,
                serialize_source=False,
            )
            for supervisor in (FailingSupervisor(), HealthySupervisor())
        ]
        threads = [threading.Thread(
            target=loop.run, args=(threading.Event(),),
            kwargs={"max_iterations": 1},
        ) for loop in loops]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=2)
        self.assertTrue(completed.is_set())

    def test_three_workers_claim_distinct_jobs_without_sqlite_collision(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = LocalSequentialJobRepository(Path(directory) / "control.sqlite3")
            repository.migrate()
            for index in range(3):
                account = f"conn_concurrent_{index}"
                repository.create_admitted_job(
                    CreateJobRequest(
                        account_key=account, company_tax_code=f"010000000{index}",
                        job_type="invoice_crawl",
                        parameters={"connection_id": account},
                        owner_id="mia-desktop-local",
                        idempotency_key_hash=hashlib.sha256(f"key-{index}".encode()).hexdigest(),
                        request_fingerprint=hashlib.sha256(f"request-{index}".encode()).hexdigest(),
                        pipeline_version=2,
                    ), (), stages=[JobStageSpec("auth")],
                )
            barrier = threading.Barrier(3)
            claimed = []
            lock = threading.Lock()

            def claim(index):
                barrier.wait(timeout=2)
                job = repository.claim_next_job(f"slot-{index}", lease_seconds=60)
                with lock:
                    claimed.append(job)

            threads = [threading.Thread(target=claim, args=(index,)) for index in range(3)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=3)
            self.assertEqual(len(claimed), 3)
            self.assertEqual(len({job.job_id for job in claimed}), 3)
            self.assertTrue(all(job.status == "running" for job in claimed))

    def test_overlapping_sync_state_poll_is_rejected_instead_of_spawning_backlog(self):
        previous = mia_runtime.data_directory
        mia_runtime.data_directory = Path(tempfile.gettempdir())
        mia_runtime._source_plan_lock.acquire()
        try:
            with self.assertRaises(mia_runtime.RpcError) as raised:
                mia_runtime.dispatch("source.sync.states", {
                    "connection_ids": ["conn_test"], "direction": "purchase",
                    "date_from": "2026-01-01", "date_to": "2026-01-31",
                })
            self.assertEqual(raised.exception.message, "source_sync_busy")
        finally:
            mia_runtime._source_plan_lock.release()
            mia_runtime.data_directory = previous


if __name__ == "__main__":
    unittest.main()

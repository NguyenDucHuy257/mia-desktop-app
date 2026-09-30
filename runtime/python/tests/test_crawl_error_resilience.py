"""Regression tests for the 4.2.3 crawl resilience fixes.

Covers the three production failures reported on 28-30/09/2026:

* a cash-register adjustment invoice whose detail carries ``hdhhdvu: null``
  must be a valid zero-line invoice, not ``source_parse_failure``;
* an HTML/empty body on HTTP 200 must be classified as a transient source
  response (retried), never as ``storage_filesystem_failure``;
* every crawl unit sleeps and retries transient failures with doubled portal
  timeouts before the job is allowed to fail.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

RUNTIME_ROOT = Path(__file__).resolve().parents[1]
VENDOR_ROOT = RUNTIME_ROOT / "vendor" / "mia_crawl_service"
for entry in (RUNTIME_ROOT, VENDOR_ROOT):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

import requests

from app.config.crawl_config import CrawlConfig
from app.parsers.invoice_detail_excel_row_builder import InvoiceDetailExcelRowBuilder
from app.worker_runtime import handler as handler_module
from app.worker_runtime.errors import ErrorClassification, TaskExecutionError, classify_task_error
from mia_optimized_source_pipeline import OptimizedInvoiceCrawlPipeline, scale_crawl_timeouts


def _adjustment_invoice_without_lines() -> dict:
    # Shape observed on the portal for sco-query invoice C26MKA/172 (tthai=3).
    return {
        "id": "b1b7c2c9", "khmshdon": 1, "khhdon": "C26MKA", "shdon": 172,
        "nbmst": "0319184471", "nmmst": "3901377078", "tthai": 3, "ttxly": 8,
        "tdlap": "2026-05-05T17:00:00Z", "nky": "2026-05-06T04:14:30Z",
        "tgtcthue": 0.0, "tgtthue": 0.0, "tgtttbso": 0.0, "ttcktmai": 0.0,
        "hdon": "01", "thdon": "Hóa đơn GTGT khởi tạo từ máy tính tiền",
        "hdhhdvu": None,
    }


class DetailRowBuilderOutcomeTests(unittest.TestCase):
    def test_null_hdhhdvu_on_a_real_invoice_is_a_valid_empty_detail(self):
        rows, outcome = InvoiceDetailExcelRowBuilder().build_rows_with_outcome(
            {"detail": _adjustment_invoice_without_lines()},
            {"nbmst": "0319184471", "khhdon": "C26MKA", "shdon": "172", "khmshdon": "1"},
        )
        self.assertEqual(rows, [])
        self.assertEqual(outcome, "valid_empty")

    def test_null_hdhhdvu_without_invoice_identity_stays_incomplete(self):
        _rows, outcome = InvoiceDetailExcelRowBuilder().build_rows_with_outcome(
            {"detail": {"hdhhdvu": None, "message": "error"}}, {},
        )
        self.assertEqual(outcome, "incomplete")

    def test_non_list_hdhhdvu_stays_incomplete(self):
        payload = _adjustment_invoice_without_lines()
        payload["hdhhdvu"] = "unexpected"
        _rows, outcome = InvoiceDetailExcelRowBuilder().build_rows_with_outcome(
            {"detail": payload}, {},
        )
        self.assertEqual(outcome, "incomplete")


class ErrorClassificationTests(unittest.TestCase):
    def _non_json_chain(self):
        response = requests.Response()
        response.status_code = 200
        response.headers["content-type"] = "text/html; charset=UTF-8"
        response._content = b"<html><body>maintenance</body></html>"
        try:
            try:
                response.json()
            except (requests.JSONDecodeError, ValueError) as error:
                raise RuntimeError("Tax portal returned non-JSON invoice detail for key=x") from error
        except RuntimeError as wrapped:
            return wrapped
        self.fail("expected a JSON decode failure")

    def test_html_body_is_a_transient_source_response_not_a_disk_failure(self):
        error = self._non_json_chain()
        classification = classify_task_error(error, uses_proxy=False, attempt_count=1)
        self.assertEqual(classification.code, "source_invalid_response")
        self.assertTrue(classification.retryable)
        self.assertNotEqual(classification.code, "storage_filesystem_failure")

    def test_plain_json_decode_error_is_classified_the_same_way(self):
        try:
            json.loads("<html>")
        except json.JSONDecodeError as error:
            classification = classify_task_error(error, uses_proxy=False)
        self.assertEqual(classification.code, "source_invalid_response")

    def test_real_os_errors_still_map_to_storage(self):
        classification = classify_task_error(PermissionError("denied"), uses_proxy=False)
        self.assertEqual(classification.code, "storage_filesystem_failure")


class AuthRetryScheduleTests(unittest.TestCase):
    def test_auth_retries_wait_minutes_not_seconds(self):
        self.assertGreaterEqual(handler_module.AUTH_RETRY_ATTEMPTS, 6)
        self.assertGreaterEqual(sum(handler_module.AUTH_RETRY_DELAYS_SECONDS), 200)

    def test_auth_retry_wait_reports_progress_and_honours_shutdown(self):
        handler = object.__new__(handler_module.InvoiceCrawlTaskHandler)
        shutdown = Mock()
        shutdown.wait.side_effect = [False, True]
        handler._shutdown_requested = shutdown
        events = []
        with self.assertRaises(handler_module.WorkerShutdownRequested):
            handler._wait_before_auth_retry(30, events.append)
        self.assertEqual(events, ["auth_retry_wait"])


class UnitRetryTests(unittest.TestCase):
    def _pipeline(self):
        pipeline = object.__new__(OptimizedInvoiceCrawlPipeline)
        pipeline.core = SimpleNamespace(crawl_config=CrawlConfig.default())
        pipeline._desktop_base_crawl_config = pipeline.core.crawl_config
        pipeline._state = {}
        pipeline._desktop_current_unit = None
        pipeline._persist = lambda **_kwargs: None
        pipeline._wait_before_source_retry = Mock()
        return pipeline

    def test_transient_failure_sleeps_retries_and_doubles_timeouts(self):
        pipeline = self._pipeline()
        base_read_timeout = pipeline.core.crawl_config.detail.read_timeout_seconds
        transient = TaskExecutionError(ErrorClassification("source_http_503", True, 20))
        attempts = []

        def operation():
            attempts.append(pipeline.core.crawl_config.detail.read_timeout_seconds)
            if len(attempts) < 3:
                raise transient
            return "ok"

        job = SimpleNamespace(job_id="job-retry")
        self.assertEqual(pipeline._run_unit_with_retry(job, "detail", operation), "ok")
        self.assertEqual(len(attempts), 3)
        self.assertEqual(attempts[0], base_read_timeout)
        self.assertEqual(attempts[1], base_read_timeout * 2)
        self.assertEqual(attempts[2], base_read_timeout * 4)
        self.assertEqual(pipeline._wait_before_source_retry.call_count, 2)
        first_delay = pipeline._wait_before_source_retry.call_args_list[0].args[1]
        second_delay = pipeline._wait_before_source_retry.call_args_list[1].args[1]
        self.assertGreaterEqual(first_delay, 5)
        self.assertGreater(second_delay, first_delay)
        self.assertEqual(pipeline._state["message"], "detail:retry_wait")

    def test_non_retryable_failure_is_raised_immediately(self):
        pipeline = self._pipeline()
        permanent = TaskExecutionError(ErrorClassification("source_parse_failure", False, 0))
        with self.assertRaises(TaskExecutionError):
            pipeline._run_unit_with_retry(SimpleNamespace(job_id="j"), "detail", Mock(side_effect=permanent))
        pipeline._wait_before_source_retry.assert_not_called()

    def test_retry_budget_is_bounded(self):
        pipeline = self._pipeline()
        transient = TaskExecutionError(ErrorClassification("source_timeout", True, 15))
        operation = Mock(side_effect=transient)
        with self.assertRaises(TaskExecutionError):
            pipeline._run_unit_with_retry(SimpleNamespace(job_id="j"), "overview", operation)
        self.assertEqual(operation.call_count, OptimizedInvoiceCrawlPipeline.UNIT_RETRY_ATTEMPTS)

    def test_wrapped_units_use_the_retry_loop_and_restore_timeouts(self):
        pipeline = self._pipeline()
        calls = []
        transient = TaskExecutionError(ErrorClassification("source_connect_failure", True, 20))

        def run_detail_unit(_job, payload, **_kwargs):
            calls.append(dict(payload))
            if len(calls) == 1:
                raise transient
            return None

        pipeline.core.run_detail_unit = run_detail_unit
        originals = pipeline._install_unit_progress_wrappers()
        pipeline.core.run_detail_unit(SimpleNamespace(job_id="j"), {
            "direction": "purchase", "query_type": "query", "nbmst": "0101",
            "khhdon": "AA/26E", "shdon": "12", "khmshdon": "1",
        })
        self.assertEqual(len(calls), 2)
        self.assertGreater(
            pipeline.core.crawl_config.detail.read_timeout_seconds,
            pipeline._desktop_base_crawl_config.detail.read_timeout_seconds,
        )
        for name, original in originals.items():
            setattr(pipeline.core, name, original)
        pipeline._restore_source_timeouts()
        self.assertIs(pipeline.core.crawl_config, pipeline._desktop_base_crawl_config)

    def test_scaled_config_keeps_validation_and_scales_every_timeout(self):
        base = CrawlConfig.default()
        scaled = scale_crawl_timeouts(base, 2)
        scaled.validate()
        self.assertEqual(scaled.detail.read_timeout_seconds, base.detail.read_timeout_seconds * 2)
        self.assertEqual(scaled.package.read_timeout_seconds, base.package.read_timeout_seconds * 2)
        self.assertEqual(
            [timeout for _size, timeout in scaled.overview.page_schedule],
            [timeout * 2 for _size, timeout in base.overview.page_schedule],
        )
        self.assertIs(scale_crawl_timeouts(base, 1), base)


if __name__ == "__main__":
    unittest.main()

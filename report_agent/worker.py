from __future__ import annotations

import logging
from pathlib import Path
import shutil
import uuid

from report_agent.artifacts import save_draft
from report_agent.credit_client import CreditClient, CreditServiceError
from report_agent.daily import build_daily_report
from report_agent.render import export_documents
from report_agent.tasks import ReportTaskStore

logger = logging.getLogger(__name__)


def run_once(store: ReportTaskStore, worker_id: str, base_url: str, token: str,
             output_dir: Path, formats=("docx", "pdf")) -> dict | None:
    task = store.claim(worker_id)
    if task is None:
        return None
    task_output = None
    try:
        client = CreditClient(base_url, token)
        report = build_daily_report(client, task["business_date"])
        store.renew(task["task_id"], worker_id)
        task_output = output_dir / task["task_id"] / uuid.uuid4().hex
        markdown, manifest = save_draft(report, task_output)
        export_documents(markdown, formats)
        store.complete(task["task_id"], worker_id, markdown, manifest)
    except Exception as exc:
        if task_output is not None:
            shutil.rmtree(task_output, ignore_errors=True)
        try:
            retryable = isinstance(exc, CreditServiceError) and exc.retryable
            status = store.retry_or_fail(task["task_id"], worker_id,
                                         f"{type(exc).__name__}: {exc}", retryable=retryable)
            logger.warning("Report task %s ended as %s (attempt %s/%s): %s",
                           task["task_id"], status, task["attempt_count"],
                           store.max_attempts, exc)
        except RuntimeError:
            # A reclaimed lease belongs to the new worker; never overwrite its state.
            logger.info("Ignoring failure from stale worker %s for task %s",
                        worker_id, task["task_id"])
    return store.get(task["task_id"])

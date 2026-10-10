"""policy_extract capacity metrics: the GPU fleet's autoscaling signal.

Published once a minute by the ``publish_capacity_metrics`` beat task. Everything
is in pages, MinerU's own invariant — docs/hour moves with document mix and has
already produced false regressions on both sides.

- ``QueueDepthDocs`` / ``LlmQueueDepth``: broker list lengths.
- ``MeanPagesPerDoc``: admitted pages / admitted docs over the sample window.
- ``BacklogPages``: QueueDepthDocs x MeanPagesPerDoc.
- ``PagesInFlight`` / ``PagesCapacity``: the page gate's live state.
- ``PagesPerHour``: pages MinerU finished over the sample window.
- ``EstimatedLagHours``: BacklogPages / PagesPerHour — what the GPU ASG's step
  policies scale on.

The gate's counters (``mineru:gate:stats``) are kept even with the gate off, so
the rates are measurable before the gate is turned on. Window deltas come from
one-minute snapshots kept in Redis, so any worker can run the task.

CloudWatch uses the host's instance role, never the ``AWS_*`` env keys: those are
the MinIO/S3 storage credentials.
"""

from __future__ import annotations

import json
import logging
import os
import time

from django.conf import settings

from core.services import mineru_gate

logger = logging.getLogger(__name__)

NAMESPACE = "InsurIQ/PolicyExtract"
SAMPLES_KEY = "policy_extract:metrics:samples"
WINDOW_SECONDS = 15 * 60
_MAX_SAMPLES = 30
# Used until the window has seen a document; the artifact's measured fleet mean.
DEFAULT_PAGES_PER_DOC = 6.25
# Lag is capped so an idle-but-nonempty fleet (no completions yet) doesn't emit
# an unbounded value into the alarms.
MAX_LAG_HOURS = 72.0


def _int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def collect() -> dict:
    """Read the broker and gate state and derive the metrics. Records a sample."""
    from policy_extract.tasks import POLICY_EXTRACT_LLM_QUEUE, POLICY_EXTRACT_QUEUE

    r = mineru_gate.get_redis()
    now = time.time()
    stats = r.hgetall(mineru_gate.KEY_STATS)
    sample = {
        "ts": now,
        "pages_admitted": _int(stats.get("pages_admitted")),
        "docs_admitted": _int(stats.get("docs_admitted")),
        "pages_done": _int(stats.get("pages_done")),
    }

    # Oldest sample still inside the window is the baseline for the rates.
    raw = r.lrange(SAMPLES_KEY, 0, -1)
    baseline = None
    for item in raw:
        try:
            s = json.loads(item)
        except ValueError:
            continue
        if now - s["ts"] <= WINDOW_SECONDS + 60:
            baseline = s
            break
    pipe = r.pipeline()
    pipe.rpush(SAMPLES_KEY, json.dumps(sample))
    pipe.ltrim(SAMPLES_KEY, -_MAX_SAMPLES, -1)
    pipe.execute()

    pages_per_doc = DEFAULT_PAGES_PER_DOC
    pages_per_hour = 0.0
    if baseline:
        elapsed = max(now - baseline["ts"], 1.0)
        d_docs = sample["docs_admitted"] - baseline["docs_admitted"]
        d_pages = sample["pages_admitted"] - baseline["pages_admitted"]
        d_done = sample["pages_done"] - baseline["pages_done"]
        if d_docs > 0 and d_pages >= 0:
            pages_per_doc = d_pages / d_docs
        if d_done > 0 and elapsed >= 60:
            pages_per_hour = d_done * 3600.0 / elapsed

    queue_docs = r.llen(POLICY_EXTRACT_QUEUE)
    backlog_pages = queue_docs * pages_per_doc
    if backlog_pages <= 0:
        lag_hours = 0.0
    elif pages_per_hour <= 0:
        lag_hours = MAX_LAG_HOURS
    else:
        lag_hours = min(backlog_pages / pages_per_hour, MAX_LAG_HOURS)

    return {
        "QueueDepthDocs": queue_docs,
        "LlmQueueDepth": r.llen(POLICY_EXTRACT_LLM_QUEUE),
        "MeanPagesPerDoc": round(pages_per_doc, 2),
        "BacklogPages": round(backlog_pages, 1),
        "PagesInFlight": mineru_gate.pages_in_flight(),
        "PagesCapacity": mineru_gate.capacity(),
        "PagesPerHour": round(pages_per_hour, 1),
        "EstimatedLagHours": round(lag_hours, 3),
    }


def _cloudwatch_client():
    import boto3
    import botocore.session

    session = botocore.session.get_session()
    # Skip the env-var provider: AWS_ACCESS_KEY_ID here is the storage key.
    session.get_component("credential_provider").remove("env")
    region = os.getenv("POLICY_EXTRACT_METRICS_REGION") or os.getenv(
        "AWS_REGION", "ap-south-1"
    )
    return boto3.Session(botocore_session=session).client(
        "cloudwatch", region_name=region
    )


def publish(metrics: dict) -> None:
    _cloudwatch_client().put_metric_data(
        Namespace=NAMESPACE,
        MetricData=[
            {"MetricName": name, "Value": float(value), "Unit": "None"}
            for name, value in metrics.items()
        ],
    )


def collect_and_publish() -> dict:
    metrics = collect()
    logger.info("policy_extract capacity: %s", metrics)
    if settings.POLICY_EXTRACT_METRICS_ENABLED:
        try:
            publish(metrics)
        except Exception:
            logger.warning("CloudWatch publish failed", exc_info=True)
    return metrics

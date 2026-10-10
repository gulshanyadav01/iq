"""Capacity metrics — the MinerU GPU fleet's autoscaling signal.

Run from the backend container:
    python manage.py test policy_extract.tests.test_capacity_metrics -v 2
"""

import json
import time
from unittest.mock import patch

from django.conf import settings
from django.test import SimpleTestCase, override_settings

from core.services import mineru_gate as gate
from policy_extract.services import capacity_metrics as cm


@override_settings(
    CELERY_BROKER_URL=settings.CELERY_BROKER_URL.rsplit("/", 1)[0] + "/15",
    POLICY_EXTRACT_MAX_PAGES_IN_FLIGHT=75,
)
class CapacityMetricsTests(SimpleTestCase):
    def setUp(self):
        gate._client = None
        self.r = gate.get_redis()
        self.r.flushdb()

    def tearDown(self):
        self.r.flushdb()
        gate._client = None

    def _baseline(self, age_s, pages_admitted, docs_admitted, pages_done):
        self.r.rpush(
            cm.SAMPLES_KEY,
            json.dumps(
                {
                    "ts": time.time() - age_s,
                    "pages_admitted": pages_admitted,
                    "docs_admitted": docs_admitted,
                    "pages_done": pages_done,
                }
            ),
        )

    def test_lag_is_backlog_pages_over_pages_per_hour(self):
        # 15 min ago: 0. Now: 100 docs / 800 pages admitted, 750 pages done.
        self._baseline(900, 0, 0, 0)
        self.r.hset(
            gate.KEY_STATS,
            mapping={"pages_admitted": 800, "docs_admitted": 100, "pages_done": 750},
        )
        self.r.rpush("policy_extract", *["m"] * 60)

        m = cm.collect()

        self.assertEqual(m["QueueDepthDocs"], 60)
        self.assertEqual(m["MeanPagesPerDoc"], 8.0)
        self.assertEqual(m["BacklogPages"], 480.0)
        self.assertAlmostEqual(m["PagesPerHour"], 3000, delta=10)
        self.assertAlmostEqual(m["EstimatedLagHours"], 0.16, delta=0.01)
        self.assertEqual(m["PagesCapacity"], 75)

    def test_capacity_follows_the_autoscaler_key(self):
        self.r.set(gate.CAPACITY_KEY, 150)
        self.assertEqual(cm.collect()["PagesCapacity"], 150)

    def test_backlog_with_no_completions_reports_capped_lag(self):
        self.r.rpush("policy_extract", "m")
        m = cm.collect()
        self.assertEqual(m["MeanPagesPerDoc"], cm.DEFAULT_PAGES_PER_DOC)
        self.assertEqual(m["EstimatedLagHours"], cm.MAX_LAG_HOURS)

    def test_empty_queue_is_zero_lag(self):
        self.assertEqual(cm.collect()["EstimatedLagHours"], 0.0)

    def test_publish_only_when_enabled(self):
        with patch.object(cm, "publish") as publish:
            with override_settings(POLICY_EXTRACT_METRICS_ENABLED=False):
                cm.collect_and_publish()
            publish.assert_not_called()
            with override_settings(POLICY_EXTRACT_METRICS_ENABLED=True):
                cm.collect_and_publish()
            publish.assert_called_once()

    def test_publish_failure_does_not_raise(self):
        with (
            override_settings(POLICY_EXTRACT_METRICS_ENABLED=True),
            patch.object(cm, "publish", side_effect=RuntimeError("no creds")),
        ):
            cm.collect_and_publish()

"""In-app MinerU GPU fleet management (`policy_extract.services.gpu_fleet`).

The capacity the page gate admits must track the GPUs actually serving, and the
scaling decision must be conservative: sustained lag to grow, sustained quiet to
shrink, never while booting or in cooldown, capacity lowered before a scale-in.

Run from the backend container:
    python manage.py test policy_extract.tests.test_gpu_fleet -v 2
"""

from unittest.mock import MagicMock, patch

from django.conf import settings
from django.test import SimpleTestCase, override_settings

from core.services import mineru_gate as gate
from policy_extract.services import gpu_fleet as fleet

FLEET_SETTINGS = dict(
    CELERY_BROKER_URL=settings.CELERY_BROKER_URL.rsplit("/", 1)[0] + "/15",
    POLICY_EXTRACT_MAX_PAGES_IN_FLIGHT=75,
    POLICY_EXTRACT_PAGES_PER_GPU=75,
    POLICY_EXTRACT_GPU_MIN=1,
    POLICY_EXTRACT_GPU_MAX=4,
    POLICY_EXTRACT_SCALE_OUT_LAG_HOURS=1.0,
    POLICY_EXTRACT_SCALE_OUT_FAST_LAG_HOURS=3.0,
    POLICY_EXTRACT_SCALE_IN_LAG_HOURS=0.25,
    POLICY_EXTRACT_SCALE_OUT_SUSTAIN_SECONDS=180,
    POLICY_EXTRACT_SCALE_IN_SUSTAIN_SECONDS=1800,
    POLICY_EXTRACT_SCALE_COOLDOWN_SECONDS=600,
    MINERU_TARGET_GROUP_ARN="",
    POLICY_EXTRACT_ADAPTIVE_CAPACITY=False,
    POLICY_EXTRACT_GPU_AUTOSCALE="off",
    MINERU_ASG_NAME="",
)


def _targets(*states):
    return {
        "TargetHealthDescriptions": [{"TargetHealth": {"State": s}} for s in states]
    }


def _asg(desired, lo=1, hi=10):
    return {
        "AutoScalingGroups": [
            {"DesiredCapacity": desired, "MinSize": lo, "MaxSize": hi}
        ]
    }


@override_settings(**FLEET_SETTINGS)
class _FleetTestCase(SimpleTestCase):
    def setUp(self):
        gate._client = None
        self.r = gate.get_redis()
        self.r.flushdb()
        self.clients = {"elbv2": MagicMock(), "autoscaling": MagicMock()}
        patcher = patch.object(fleet, "aws_client", side_effect=self.clients.get)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self.r.flushdb()
        gate._client = None


class SyncCapacityTests(_FleetTestCase):
    @override_settings(MINERU_TARGET_GROUP_ARN="arn:tg")
    def test_capacity_counts_only_healthy_targets(self):
        # A booting GPU (initial) and a GPU being scaled in (draining) don't count.
        self.clients["elbv2"].describe_target_health.return_value = _targets(
            "healthy", "healthy", "initial", "draining"
        )
        out = fleet.sync_capacity(self.r)
        self.assertEqual(out["capacity"], 150)
        self.assertEqual((out["healthy"], out["draining"]), (2, 1))
        self.assertEqual(gate.capacity(), 150)

    @override_settings(MINERU_TARGET_GROUP_ARN="arn:tg")
    def test_no_healthy_gpu_parks_documents_in_the_gate(self):
        self.clients["elbv2"].describe_target_health.return_value = _targets(
            "unhealthy"
        )
        self.assertEqual(fleet.sync_capacity(self.r)["capacity"], 1)

    @override_settings(
        MINERU_TARGET_GROUP_ARN="arn:tg", POLICY_EXTRACT_ADAPTIVE_CAPACITY=True
    )
    def test_aws_failure_falls_back_to_adaptive(self):
        self.clients["elbv2"].describe_target_health.side_effect = RuntimeError("iam")
        self.assertEqual(fleet.sync_capacity(self.r)["source"], "adaptive")

    @override_settings(MINERU_TARGET_GROUP_ARN="arn:tg")
    def test_aws_failure_without_adaptive_keeps_capacity(self):
        self.r.set(gate.CAPACITY_KEY, 150)
        self.clients["elbv2"].describe_target_health.side_effect = RuntimeError("iam")
        out = fleet.sync_capacity(self.r)
        self.assertEqual((out["source"], out["capacity"]), ("static", 150))

    def test_static_leaves_the_key_alone(self):
        self.r.set(gate.CAPACITY_KEY, 90)
        self.assertEqual(fleet.sync_capacity(self.r)["capacity"], 90)
        self.clients["elbv2"].describe_target_health.assert_not_called()


@override_settings(POLICY_EXTRACT_ADAPTIVE_CAPACITY=True)
class AdaptiveCapacityTests(_FleetTestCase):
    def _tick(self, **bump):
        for k, v in bump.items():
            self.r.hincrby(gate.KEY_STATS, k, v)
        return fleet.sync_capacity(self.r)

    def test_unmet_demand_grows_overload_shrinks(self):
        self._tick()  # baseline snapshot
        self.assertEqual(self._tick(refused=40)["capacity"], 80)
        self.assertEqual(self._tick(refused=40)["capacity"], 85)
        out = self._tick(refused=40, overload=2)
        self.assertEqual(out["capacity"], 63)  # 85 x 0.75
        self.assertIn("overload", out["reason"])
        self.assertEqual(self._tick()["capacity"], 63)  # steady

    def test_bounded_by_the_gpu_budget(self):
        self.r.set(gate.CAPACITY_KEY, 298)
        self._tick()
        self.assertEqual(self._tick(refused=5)["capacity"], 300)  # 4 GPUs x 75
        self.assertEqual(self._tick(refused=5)["capacity"], 300)


class DecideTests(_FleetTestCase):
    def _m(self, lag, in_flight=10, cap=75):
        return {
            "EstimatedLagHours": lag,
            "PagesInFlight": in_flight,
            "PagesCapacity": cap,
        }

    def test_scale_out_needs_sustained_lag(self):
        self.assertEqual(fleet.decide(self.r, self._m(2), 1, 1, 1000)["target"], 1)
        self.assertEqual(fleet.decide(self.r, self._m(2), 1, 1, 1100)["target"], 1)
        self.assertEqual(fleet.decide(self.r, self._m(2), 1, 1, 1180)["target"], 2)

    def test_a_dip_resets_the_sustain_clock(self):
        fleet.decide(self.r, self._m(2), 1, 1, 1000)
        fleet.decide(self.r, self._m(0.5), 1, 1, 1100)
        self.assertEqual(fleet.decide(self.r, self._m(2), 1, 1, 1200)["target"], 1)

    def test_unknown_lag_holds_without_touching_the_sustain_clock(self):
        # Just after an idle spell the lag reads as its 72h cap with nothing
        # finished yet; that must not count as "far behind".
        unknown = {**self._m(72), "LagKnown": 0}
        fleet.decide(self.r, self._m(2), 1, 1, 1000)
        out = fleet.decide(self.r, unknown, 1, 1, 1100)
        self.assertEqual((out["target"], out["reason"][:11]), (1, "lag unknown"))
        self.assertIsNone(self.r.get(fleet.KEY_OUT_SINCE + ":fast"))
        # ...and the +1 clock that was already running is not reset.
        self.assertEqual(fleet.decide(self.r, self._m(2), 1, 1, 1180)["target"], 2)

    def test_fast_lag_adds_two(self):
        fleet.decide(self.r, self._m(5), 1, 1, 1000)
        self.assertEqual(fleet.decide(self.r, self._m(5), 1, 1, 1180)["target"], 3)

    def test_holds_while_a_gpu_is_still_booting(self):
        fleet.decide(self.r, self._m(5), 2, 1, 1000)
        out = fleet.decide(self.r, self._m(5), 2, 1, 1200)
        self.assertEqual(out["target"], 2)
        self.assertIn("boot", out["reason"])

    def test_cooldown_after_an_action(self):
        self.r.set(fleet.KEY_LAST_ACTION, 900)
        fleet.decide(self.r, self._m(5), 1, 1, 1000)
        self.assertEqual(
            fleet.decide(self.r, self._m(5), 1, 1, 1180)["reason"], "cooldown"
        )

    def test_scale_in_needs_low_lag_and_a_half_empty_gate_for_30_min(self):
        quiet = self._m(0.1, in_flight=20, cap=150)
        fleet.decide(self.r, quiet, 2, 2, 0)
        self.assertEqual(fleet.decide(self.r, quiet, 2, 2, 1700)["target"], 2)
        self.assertEqual(fleet.decide(self.r, quiet, 2, 2, 1800)["target"], 1)

    def test_busy_gate_blocks_scale_in_even_with_low_lag(self):
        busy = self._m(0.1, in_flight=140, cap=150)
        fleet.decide(self.r, busy, 2, 2, 0)
        self.assertEqual(fleet.decide(self.r, busy, 2, 2, 4000)["target"], 2)


class ScaleTests(_FleetTestCase):
    LAGGING = {"EstimatedLagHours": 5, "PagesInFlight": 75, "PagesCapacity": 75}
    QUIET = {"EstimatedLagHours": 0, "PagesInFlight": 0, "PagesCapacity": 150}

    def _prime(self, metrics, desired, healthy, t0=0):
        self.clients["autoscaling"].describe_auto_scaling_groups.return_value = _asg(
            desired
        )
        fleet.decide(self.r, metrics, desired, healthy, t0)

    @override_settings(POLICY_EXTRACT_GPU_AUTOSCALE="watch", MINERU_ASG_NAME="mineru")
    def test_watch_mode_records_but_never_calls_aws(self):
        self._prime(self.LAGGING, 1, 1)
        out = fleet.scale(self.r, self.LAGGING, {"healthy": 1}, 1000)
        self.assertEqual((out["target"], out["acted"]), (3, False))
        self.clients["autoscaling"].set_desired_capacity.assert_not_called()
        self.assertIn('"target": 3', self.r.get(fleet.KEY_LAST_DECISION))

    @override_settings(POLICY_EXTRACT_GPU_AUTOSCALE="on", MINERU_ASG_NAME="mineru")
    def test_on_mode_sets_desired_within_the_budget(self):
        self._prime(self.LAGGING, 3, 3)
        out = fleet.scale(self.r, self.LAGGING, {"healthy": 3}, 1000)
        self.assertEqual(out["target"], 4)  # 3 + 2 clamped to GPU_MAX
        self.clients["autoscaling"].set_desired_capacity.assert_called_once_with(
            AutoScalingGroupName="mineru", DesiredCapacity=4, HonorCooldown=False
        )

    @override_settings(POLICY_EXTRACT_GPU_AUTOSCALE="on", MINERU_ASG_NAME="mineru")
    def test_scale_in_lowers_capacity_before_telling_the_asg(self):
        self.r.set(gate.CAPACITY_KEY, 150)
        seen = {}
        self.clients["autoscaling"].set_desired_capacity.side_effect = (
            lambda **kw: seen.setdefault("cap", gate.capacity())
        )
        self._prime(self.QUIET, 2, 2)
        out = fleet.scale(self.r, self.QUIET, {"healthy": 2}, 2000)
        self.assertEqual(out["target"], 1)
        self.assertEqual(seen["cap"], 75)

    @override_settings(POLICY_EXTRACT_GPU_AUTOSCALE="on", MINERU_ASG_NAME="mineru")
    def test_never_below_the_minimum(self):
        self._prime(self.QUIET, 1, 1)
        out = fleet.scale(self.r, self.QUIET, {"healthy": 1}, 2000)
        self.assertEqual(out["target"], 1)
        self.clients["autoscaling"].set_desired_capacity.assert_not_called()

    def test_off_does_nothing(self):
        self.assertIsNone(fleet.scale(self.r, self.LAGGING, {}, 0))


class RunCycleTests(_FleetTestCase):
    @override_settings(MINERU_TARGET_GROUP_ARN="arn:tg")
    def test_one_worker_per_tick(self):
        self.clients["elbv2"].describe_target_health.return_value = _targets("healthy")
        first = fleet.run_cycle()
        self.assertEqual(first["sync"]["capacity"], 75)
        self.assertEqual(first["metrics"]["HealthyGPUs"], 1)
        self.assertIn("skipped", fleet.run_cycle())

    def test_a_scaling_error_still_publishes_metrics(self):
        with (
            override_settings(POLICY_EXTRACT_GPU_AUTOSCALE="on", MINERU_ASG_NAME="m"),
            patch.object(fleet, "scale", side_effect=RuntimeError("throttled")),
            patch(
                "policy_extract.services.capacity_metrics.publish_if_enabled"
            ) as publish,
        ):
            out = fleet.run_cycle()
        self.assertIn("error", out["fleet"])
        publish.assert_called_once()

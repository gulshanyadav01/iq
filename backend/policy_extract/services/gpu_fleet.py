"""MinerU GPU fleet management, run from the app — no Lambda, no alarms.

One beat task (``capacity_cycle``, every ``POLICY_EXTRACT_CAPACITY_CYCLE_SECONDS``)
runs three steps under a Redis lock so only one worker does it per tick:

1. **Sync capacity** — keep ``mineru:pages_capacity`` (what the page gate admits)
   equal to the GPUs actually serving:
   - ``alb``: when ``MINERU_TARGET_GROUP_ARN`` is set, count the target group's
     ``healthy`` targets. A new GPU counts only once its model is loaded and the
     ALB marks it healthy; a GPU being scaled in is deregistered first and shows
     as ``draining``, so it stops counting while it still finishes its in-flight
     requests (the ALB deregistration delay must stay >= MinerU's 420s
     proxy_read_timeout). Zero healthy targets -> capacity 1, so documents wait in
     the gate instead of burning their MinerU retries against a dead fleet.
   - ``adaptive`` (``POLICY_EXTRACT_ADAPTIVE_CAPACITY``; also the fallback when the
     AWS call fails): AIMD on MinerU's own behaviour. Any overload (429/503/504 or
     a timeout) since the last tick cuts capacity by a quarter; unmet demand (the
     gate turned documents away) with no overload adds a few pages. Needs no cloud
     access, but finds new GPUs only by probing.
   - ``static``: leave the key alone (``POLICY_EXTRACT_MAX_PAGES_IN_FLIGHT`` or a
     hand-set value).
2. **Metrics** — ``capacity_metrics.collect`` (and CloudWatch when enabled).
3. **Scale** (``POLICY_EXTRACT_GPU_AUTOSCALE``: ``off`` | ``watch`` | ``on``) — set
   the MinerU ASG's desired capacity from ``EstimatedLagHours``. ``watch`` logs and
   records what it *would* do without calling AWS. Rules:
   - out: lag above ``SCALE_OUT_LAG_HOURS`` (+1) or ``SCALE_OUT_FAST_LAG_HOURS``
     (+2), sustained ``SCALE_OUT_SUSTAIN_SECONDS``. While the lag is unknown (a
     backlog but nothing finished yet, e.g. just after an idle night) it holds;
   - in: lag below ``SCALE_IN_LAG_HOURS`` and the gate under half full, sustained
     ``SCALE_IN_SUSTAIN_SECONDS``. Capacity is lowered *before* the ASG is told,
     so the gate never admits work for a GPU that is about to leave;
   - never while a previous scale-out is still booting (desired > healthy), never
     within ``SCALE_COOLDOWN_SECONDS`` of the last action, always within
     ``[GPU_MIN, GPU_MAX]`` and the ASG's own min/max.

If the cycle stops running (beat or the default worker down), the GPU count just
stays where it is — safe — and the ASG's min/max still hold. Time-of-day baselines
belong in ASG scheduled actions, which are native to the ASG and need nothing else.

IAM on the worker host role: ``elasticloadbalancing:DescribeTargetHealth``,
``autoscaling:DescribeAutoScalingGroups``, ``autoscaling:SetDesiredCapacity`` (only
for ``on``), ``cloudwatch:PutMetricData`` (only with metrics enabled).
"""

from __future__ import annotations

import json
import logging
import os
import time

from django.conf import settings

from core.services import mineru_gate as gate

logger = logging.getLogger(__name__)

_P = "gpu_fleet:"
KEY_LOCK = _P + "lock"
KEY_OUT_SINCE = _P + "out_since"
KEY_IN_SINCE = _P + "in_since"
KEY_LAST_ACTION = _P + "last_action_ts"
KEY_LAST_DECISION = _P + "last_decision"
KEY_ADAPTIVE_PREV = _P + "adaptive_prev"

ADAPTIVE_DECREASE = 0.75
ADAPTIVE_STEP_PAGES = 5


def aws_client(service: str):
    """boto3 client on the host's instance role. The env-var credential provider is
    skipped: AWS_ACCESS_KEY_ID here is the MinIO/S3 storage key."""
    import boto3
    import botocore.session

    session = botocore.session.get_session()
    session.get_component("credential_provider").remove("env")
    region = os.getenv("POLICY_EXTRACT_METRICS_REGION") or os.getenv(
        "AWS_REGION", "ap-south-1"
    )
    return boto3.Session(botocore_session=session).client(service, region_name=region)


# --- 1. capacity sync -------------------------------------------------------


def _alb_counts() -> tuple[int, int]:
    """(healthy, draining) targets in the MinerU target group."""
    resp = aws_client("elbv2").describe_target_health(
        TargetGroupArn=settings.MINERU_TARGET_GROUP_ARN
    )
    states = [d["TargetHealth"]["State"] for d in resp["TargetHealthDescriptions"]]
    return states.count("healthy"), states.count("draining")


def _bounds() -> tuple[int, int]:
    per_gpu = settings.POLICY_EXTRACT_PAGES_PER_GPU
    return 1, per_gpu * max(settings.POLICY_EXTRACT_GPU_MAX, 1)


def _adaptive_step(r) -> dict:
    stats = r.hgetall(gate.KEY_STATS)
    now = {
        "overload": int(stats.get("overload", 0)),
        "refused": int(stats.get("refused", 0)),
    }
    prev = json.loads(r.get(KEY_ADAPTIVE_PREV) or "null") or now
    r.set(KEY_ADAPTIVE_PREV, json.dumps(now))
    d_overload = now["overload"] - prev["overload"]
    d_refused = now["refused"] - prev["refused"]

    lo, hi = _bounds()
    cap = gate.capacity()
    if d_overload > 0:
        new = int(cap * ADAPTIVE_DECREASE)
        reason = f"{d_overload} overloads"
    elif d_refused > 0:
        new = cap + ADAPTIVE_STEP_PAGES
        reason = "unmet demand"
    else:
        new, reason = cap, "steady"
    new = min(max(new, lo), hi)
    if new != cap:
        r.set(gate.CAPACITY_KEY, new)
    return {"source": "adaptive", "capacity": new, "reason": reason}


def sync_capacity(r) -> dict:
    if settings.MINERU_TARGET_GROUP_ARN:
        try:
            healthy, draining = _alb_counts()
            cap = healthy * settings.POLICY_EXTRACT_PAGES_PER_GPU if healthy else 1
            r.set(gate.CAPACITY_KEY, cap)
            return {
                "source": "alb",
                "capacity": cap,
                "healthy": healthy,
                "draining": draining,
            }
        except Exception:
            logger.warning(
                "GPU fleet: target-health lookup failed; %s",
                "using adaptive capacity"
                if settings.POLICY_EXTRACT_ADAPTIVE_CAPACITY
                else "keeping current capacity",
                exc_info=True,
            )
    if settings.POLICY_EXTRACT_ADAPTIVE_CAPACITY:
        return _adaptive_step(r)
    return {"source": "static", "capacity": gate.capacity()}


# --- 3. scaling decision ----------------------------------------------------


def _sustained(r, key: str, condition: bool, seconds: int, now: float) -> bool:
    if not condition:
        r.delete(key)
        return False
    since = r.get(key)
    if since is None:
        r.set(key, now)
        return seconds <= 0
    return now - float(since) >= seconds


def decide(r, metrics: dict, desired: int, healthy: int | None, now: float) -> dict:
    """Pure-ish decision (state only in Redis). Returns {target, reason}."""
    lag = metrics["EstimatedLagHours"]
    hold = {"target": desired, "reason": "hold"}
    if not metrics.get("LagKnown", 1):
        # Backlog but no completions in the window yet: pages/hour is unmeasured
        # and the lag is just its cap. Hold without starting or resetting any
        # sustain clock; the next ticks will have a real number.
        return {**hold, "reason": "lag unknown (no completions yet)"}

    # Sustain bookkeeping first, so a held tick still accrues time.
    out_fast = _sustained(
        r,
        KEY_OUT_SINCE + ":fast",
        lag > settings.POLICY_EXTRACT_SCALE_OUT_FAST_LAG_HOURS,
        settings.POLICY_EXTRACT_SCALE_OUT_SUSTAIN_SECONDS,
        now,
    )
    out = _sustained(
        r,
        KEY_OUT_SINCE,
        lag > settings.POLICY_EXTRACT_SCALE_OUT_LAG_HOURS,
        settings.POLICY_EXTRACT_SCALE_OUT_SUSTAIN_SECONDS,
        now,
    )
    quiet = lag < settings.POLICY_EXTRACT_SCALE_IN_LAG_HOURS and (
        metrics["PagesInFlight"] < 0.5 * max(metrics["PagesCapacity"], 1)
    )
    scale_in = _sustained(
        r, KEY_IN_SINCE, quiet, settings.POLICY_EXTRACT_SCALE_IN_SUSTAIN_SECONDS, now
    )

    if healthy is not None and desired > healthy:
        return {**hold, "reason": f"waiting for {desired - healthy} GPU(s) to boot"}
    last = float(r.get(KEY_LAST_ACTION) or 0)
    if now - last < settings.POLICY_EXTRACT_SCALE_COOLDOWN_SECONDS:
        return {**hold, "reason": "cooldown"}
    if out_fast:
        return {"target": desired + 2, "reason": f"lag {lag:.2f}h (fast)"}
    if out:
        return {"target": desired + 1, "reason": f"lag {lag:.2f}h"}
    if scale_in:
        return {"target": desired - 1, "reason": f"quiet (lag {lag:.2f}h)"}
    return hold


def _asg_state() -> tuple[int, int, int]:
    resp = aws_client("autoscaling").describe_auto_scaling_groups(
        AutoScalingGroupNames=[settings.MINERU_ASG_NAME]
    )
    g = resp["AutoScalingGroups"][0]
    return g["DesiredCapacity"], g["MinSize"], g["MaxSize"]


def scale(r, metrics: dict, sync: dict, now: float) -> dict | None:
    mode = settings.POLICY_EXTRACT_GPU_AUTOSCALE
    if mode not in ("watch", "on") or not settings.MINERU_ASG_NAME:
        return None
    desired, asg_min, asg_max = _asg_state()
    lo = max(settings.POLICY_EXTRACT_GPU_MIN, asg_min)
    hi = min(settings.POLICY_EXTRACT_GPU_MAX, asg_max)
    decision = decide(r, metrics, desired, sync.get("healthy"), now)
    target = min(max(decision["target"], lo), hi)
    result = {
        "mode": mode,
        "desired": desired,
        "target": target,
        "reason": decision["reason"],
        "acted": False,
    }
    if target != desired:
        if mode == "on":
            if target < desired:
                # Shrink the gate first so nothing new is admitted for the GPU
                # the ASG is about to drain.
                r.set(
                    gate.CAPACITY_KEY,
                    max(target * settings.POLICY_EXTRACT_PAGES_PER_GPU, 1),
                )
            aws_client("autoscaling").set_desired_capacity(
                AutoScalingGroupName=settings.MINERU_ASG_NAME,
                DesiredCapacity=target,
                HonorCooldown=False,
            )
            result["acted"] = True
        r.set(KEY_LAST_ACTION, now)
        r.delete(KEY_OUT_SINCE, KEY_OUT_SINCE + ":fast", KEY_IN_SINCE)
        logger.warning(
            "GPU fleet %s: %d -> %d GPUs (%s)",
            "scaled" if mode == "on" else "would scale",
            desired,
            target,
            decision["reason"],
        )
    r.set(KEY_LAST_DECISION, json.dumps({**result, "ts": now}))
    return result


# --- the cycle --------------------------------------------------------------


def run_cycle() -> dict:
    from policy_extract.services import capacity_metrics

    r = gate.get_redis()
    ttl = max(int(settings.POLICY_EXTRACT_CAPACITY_CYCLE_SECONDS) - 1, 1)
    if not r.set(KEY_LOCK, os.getpid(), nx=True, ex=ttl):
        return {"skipped": "another worker ran this tick"}

    now = time.time()
    sync = sync_capacity(r)
    metrics = capacity_metrics.collect()
    try:
        fleet = scale(r, metrics, sync, now)
    except Exception:
        logger.warning("GPU fleet: scaling step failed", exc_info=True)
        fleet = {"error": "scaling step failed"}

    if sync.get("healthy") is not None:
        metrics["HealthyGPUs"] = sync["healthy"]
    if fleet and "target" in fleet:
        metrics["DesiredGPUs"] = fleet["desired"]
        metrics["TargetGPUs"] = fleet["target"]
    capacity_metrics.publish_if_enabled(metrics)
    logger.info("capacity cycle: sync=%s fleet=%s metrics=%s", sync, fleet, metrics)
    return {"sync": sync, "fleet": fleet, "metrics": metrics}

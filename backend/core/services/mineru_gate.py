"""Pages-in-flight admission control in front of MinerU.

MinerU's real constraint is pages in flight, not requests: its throughput sweep
peaks near 75 pages, and a document-count cap overshoots it whenever the mix runs
heavy (11.8 pages/doc x 8 docs is already ~92 pages). So a policy PDF's MinerU
call is admitted only while ``pages_in_flight + doc_pages <= capacity``.

State lives in the broker Redis so every worker host shares one budget:

- ``res`` / ``pages``: admitted reservations (ZSET token -> lease expiry ms, HASH
  token -> pages). A reservation is a short lease (``_LEASE_MS``) that a daemon
  thread renews every ``_RENEW_EVERY_S`` while the MinerU call runs. A SIGKILLed or
  OOM-killed child stops renewing, so its pages return within one lease. A fixed
  TTL spanning the whole request timeout froze the entire capacity for six
  minutes after a worker crash in end-to-end testing.
- ``queue`` / ``hb`` / ``since``: the FIFO wait line (ZSET token -> ticket number),
  each waiter's last poll and when it joined. A waiter that stops polling (killed
  mid-wait) drops off the line after ``_TICKET_TTL_MS``.

Admission is FIFO with bounded backfill. The head of the line is admitted as soon
as its pages fit. A waiter behind it may jump ahead only if it fits in the pages
free *now* and the head has waited less than ``POLICY_EXTRACT_GATE_BACKFILL_SECONDS``.
Strict FIFO wastes the gap a large head leaves (end-to-end against a mock MinerU,
it held occupancy near 74%); the bound means a 29-page document still can't be
starved by a stream of small ones — once its window passes, the line is strict
FIFO again until it is admitted. ``0`` disables backfill.

Capacity is read from ``mineru:pages_capacity`` when set — the autoscaling hook:
the GPU fleet's capacity-sync job writes ``healthy_nodes x C_node`` there — and
falls back to ``POLICY_EXTRACT_MAX_PAGES_IN_FLIGHT``. A document larger than the
whole capacity is admitted alone once nothing else is in flight.

The reservation covers only the MinerU POST. Holding it across the Gemini stages
would reintroduce the ~22% occupancy loss this gate exists to remove.

``POLICY_EXTRACT_PAGE_GATE`` (default off) is the no-redeploy rollback switch.
Off, nothing waits, but the stats counters are still bumped for the metrics.
Redis errors fail open — the gate is a throughput optimisation, and the broker
being down stops the workers anyway.
"""

from __future__ import annotations

import logging
import os
import random
import threading
import time
import uuid
from contextlib import contextmanager
from typing import Iterator

import redis
from django.conf import settings

from core.services.mineru import GateWaitTimeout

logger = logging.getLogger(__name__)

CAPACITY_KEY = "mineru:pages_capacity"
_PREFIX = "mineru:gate:"
KEY_RES = _PREFIX + "res"
KEY_PAGES = _PREFIX + "pages"
KEY_QUEUE = _PREFIX + "queue"
KEY_HB = _PREFIX + "hb"
KEY_SEQ = _PREFIX + "seq"
KEY_SINCE = _PREFIX + "since"
# Cumulative counters (pages_admitted, docs_admitted, pages_done) for the
# capacity-metrics publisher.
KEY_STATS = _PREFIX + "stats"

_LEASE_MS = 60_000
_RENEW_EVERY_S = 15.0
_TICKET_TTL_MS = 15_000
_POLL_MIN_S = 0.5
_POLL_MAX_S = 1.0

# Shared preamble: server clock (so worker clock skew can't expire a live
# reservation), then reap expired reservations and abandoned tickets.
_REAP = """
pcall(redis.replicate_commands)
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
local expired = redis.call('ZRANGEBYSCORE', KEYS[1], '-inf', now)
for _, tok in ipairs(expired) do redis.call('HDEL', KEYS[2], tok) end
if #expired > 0 then redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now) end
local stale = redis.call('ZRANGEBYSCORE', KEYS[4], '-inf', now - tonumber(ARGV[6]))
for _, tok in ipairs(stale) do
  redis.call('ZREM', KEYS[3], tok)
  redis.call('ZREM', KEYS[4], tok)
  redis.call('HDEL', KEYS[8], tok)
end
local in_flight = 0
for _, v in ipairs(redis.call('HVALS', KEYS[2])) do in_flight = in_flight + tonumber(v) end
"""

# KEYS: res, pages, queue, hb, seq, capacity, stats, since
# ARGV: token, pages, default_capacity, reserve, reservation_ttl_ms, ticket_ttl_ms,
#       backfill_ms
# Returns {admitted (0|1), in_flight, capacity}.
_ACQUIRE = (
    _REAP
    + """
local token = ARGV[1]
local pages = tonumber(ARGV[2])
local cap = tonumber(redis.call('GET', KEYS[6]) or ARGV[3]) or tonumber(ARGV[3])
cap = math.max(cap - tonumber(ARGV[4]), 1)
if not redis.call('ZSCORE', KEYS[3], token) then
  redis.call('ZADD', KEYS[3], redis.call('INCR', KEYS[5]), token)
  redis.call('HSET', KEYS[8], token, now)
end
redis.call('ZADD', KEYS[4], now, token)
local head = redis.call('ZRANGE', KEYS[3], 0, 0)[1]
local fits = in_flight + pages <= cap
local ok
if head == token then
  ok = fits or in_flight == 0
else
  local head_since = tonumber(redis.call('HGET', KEYS[8], head) or now) or now
  ok = fits and (now - head_since) < tonumber(ARGV[7])
end
if ok then
  redis.call('ZREM', KEYS[3], token)
  redis.call('ZREM', KEYS[4], token)
  redis.call('HDEL', KEYS[8], token)
  redis.call('ZADD', KEYS[1], now + tonumber(ARGV[5]), token)
  redis.call('HSET', KEYS[2], token, pages)
  redis.call('HINCRBY', KEYS[7], 'pages_admitted', pages)
  redis.call('HINCRBY', KEYS[7], 'docs_admitted', 1)
  return {1, in_flight + pages, cap}
end
return {0, in_flight, cap}
"""
)

# KEYS: res, pages   ARGV: token, lease_ms
_RENEW = """
pcall(redis.replicate_commands)
if redis.call('HEXISTS', KEYS[2], ARGV[1]) == 1 then
  local t = redis.call('TIME')
  local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
  redis.call('ZADD', KEYS[1], 'XX', now + tonumber(ARGV[2]), ARGV[1])
  return 1
end
return 0
"""

# KEYS: res, pages, stats
_RELEASE = """
local pages = redis.call('HGET', KEYS[2], ARGV[1])
if pages then
  redis.call('HDEL', KEYS[2], ARGV[1])
  redis.call('ZREM', KEYS[1], ARGV[1])
  redis.call('HINCRBY', KEYS[3], 'pages_done', tonumber(pages))
  return 1
end
return 0
"""

_client: redis.Redis | None = None
_client_pid: int | None = None
_scripts: dict[str, object] = {}


def get_redis() -> redis.Redis:
    """Per-process client on the broker Redis (a prefork child never reuses the
    parent's sockets)."""
    global _client, _client_pid
    pid = os.getpid()
    if _client is None or _client_pid != pid:
        _client = redis.Redis.from_url(
            settings.CELERY_BROKER_URL, socket_timeout=5, decode_responses=True
        )
        _client_pid = pid
        _scripts.clear()
    return _client


def _script(name: str, body: str):
    client = get_redis()
    if name not in _scripts:
        _scripts[name] = client.register_script(body)
    return _scripts[name]


def capacity() -> int:
    """Configured page capacity before the reserve: the autoscaler's key, else env."""
    default = settings.POLICY_EXTRACT_MAX_PAGES_IN_FLIGHT
    try:
        raw = get_redis().get(CAPACITY_KEY)
        return int(raw) if raw else default
    except (redis.RedisError, ValueError):
        return default


def pages_in_flight() -> int:
    vals = get_redis().hvals(KEY_PAGES)
    return sum(int(v) for v in vals)


def try_acquire(token: str, pages: int) -> tuple[bool, int, int]:
    """One admission attempt. Joins the wait line on first call; returns
    (admitted, in_flight, effective_capacity)."""
    admitted, in_flight, cap = _script("acquire", _ACQUIRE)(
        keys=[
            KEY_RES,
            KEY_PAGES,
            KEY_QUEUE,
            KEY_HB,
            KEY_SEQ,
            CAPACITY_KEY,
            KEY_STATS,
            KEY_SINCE,
        ],
        args=[
            token,
            pages,
            settings.POLICY_EXTRACT_MAX_PAGES_IN_FLIGHT,
            settings.POLICY_EXTRACT_GATE_RESERVE_PAGES,
            _LEASE_MS,
            _TICKET_TTL_MS,
            int(settings.POLICY_EXTRACT_GATE_BACKFILL_SECONDS * 1000),
        ],
    )
    return bool(admitted), int(in_flight), int(cap)


def release(token: str) -> None:
    _script("release", _RELEASE)(keys=[KEY_RES, KEY_PAGES, KEY_STATS], args=[token])


def renew(token: str) -> bool:
    """Extend a held reservation's lease. False once it is gone (released/reaped)."""
    return bool(
        _script("renew", _RENEW)(keys=[KEY_RES, KEY_PAGES], args=[token, _LEASE_MS])
    )


def _keep_alive(token: str, stop: threading.Event) -> None:
    while not stop.wait(_RENEW_EVERY_S):
        try:
            if not renew(token):
                return
        except redis.RedisError:
            logger.debug("MinerU gate lease renewal failed", exc_info=True)


def _count(**fields: int) -> None:
    """Best-effort bump of the stats counters (the ungated path)."""
    try:
        pipe = get_redis().pipeline()
        for field, n in fields.items():
            pipe.hincrby(KEY_STATS, field, n)
        pipe.execute()
    except redis.RedisError:
        logger.debug("MinerU gate stats unavailable", exc_info=True)


def leave_queue(token: str) -> None:
    pipe = get_redis().pipeline()
    pipe.zrem(KEY_QUEUE, token)
    pipe.zrem(KEY_HB, token)
    pipe.hdel(KEY_SINCE, token)
    pipe.execute()


@contextmanager
def page_gate(pages: int | None) -> Iterator[None]:
    """Hold ``pages`` of MinerU capacity for the duration of the block.

    No-op when ``POLICY_EXTRACT_PAGE_GATE`` is off. Waits FIFO up to
    ``POLICY_EXTRACT_GATE_MAX_WAIT`` seconds, then raises ``GateWaitTimeout`` —
    transient per ``is_retryable``, so the task requeues instead of pinning a
    worker child.
    """
    pages = max(int(pages or 1), 1)
    if not settings.POLICY_EXTRACT_PAGE_GATE:
        # Ungated, but still counted: the capacity metrics (pages/doc, pages/hour)
        # must be measurable before the gate is turned on.
        _count(pages_admitted=pages, docs_admitted=1)
        try:
            yield
        finally:
            _count(pages_done=pages)
        return

    token = uuid.uuid4().hex
    deadline = time.monotonic() + settings.POLICY_EXTRACT_GATE_MAX_WAIT
    admitted = False
    try:
        while True:
            ok, in_flight, cap = try_acquire(token, pages)
            if ok:
                admitted = True
                break
            if time.monotonic() >= deadline:
                leave_queue(token)
                raise GateWaitTimeout(
                    f"MinerU page gate: waited "
                    f"{settings.POLICY_EXTRACT_GATE_MAX_WAIT}s for {pages} pages "
                    f"({in_flight}/{cap} in flight)"
                )
            time.sleep(random.uniform(_POLL_MIN_S, _POLL_MAX_S))
    except redis.RedisError:
        logger.warning("MinerU page gate unavailable; admitting ungated", exc_info=True)
    except BaseException:
        # SoftTimeLimitExceeded or a shutdown mid-wait: don't block the line head.
        if not admitted:
            try:
                leave_queue(token)
            except redis.RedisError:
                pass  # the ticket heartbeat lapses on its own
        raise

    stop = threading.Event()
    if admitted:
        threading.Thread(
            target=_keep_alive, args=(token, stop), daemon=True, name="mineru-lease"
        ).start()
    try:
        yield
    finally:
        stop.set()
        if admitted:
            try:
                release(token)
            except redis.RedisError:
                logger.warning(
                    "MinerU page gate release failed; reservation expires with its lease",
                    exc_info=True,
                )

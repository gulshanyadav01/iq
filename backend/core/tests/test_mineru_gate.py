"""The pages-in-flight gate in front of MinerU (`core.services.mineru_gate`).

MinerU's constraint is pages in flight, not requests, so a document's MinerU call is
admitted only while `in_flight + pages <= capacity`, FIFO so a large document is never
starved, with reservations that expire if their holder is killed. Runs against a
scratch db on the dev Redis (the gate's whole job is the Lua scripts, so mocking Redis
would test nothing).

Run from the backend container:
    python manage.py test core.tests.test_mineru_gate -v 2
"""

from django.conf import settings
from django.test import SimpleTestCase, override_settings

from core.services import mineru_gate as gate
from core.services.mineru import GateWaitTimeout, is_retryable

_SCRATCH_DB = 15


def _scratch_url() -> str:
    base = settings.CELERY_BROKER_URL.rsplit("/", 1)[0]
    return f"{base}/{_SCRATCH_DB}"


class _GateTestCase(SimpleTestCase):
    def setUp(self):
        self._settings = override_settings(
            CELERY_BROKER_URL=_scratch_url(),
            POLICY_EXTRACT_PAGE_GATE=True,
            POLICY_EXTRACT_MAX_PAGES_IN_FLIGHT=20,
            POLICY_EXTRACT_GATE_MAX_WAIT=0,
            POLICY_EXTRACT_GATE_RESERVE_PAGES=0,
            # Strict FIFO unless a test opts into backfill.
            POLICY_EXTRACT_GATE_BACKFILL_SECONDS=0,
        )
        self._settings.enable()
        gate._client = None
        self.r = gate.get_redis()
        self.r.flushdb()

    def tearDown(self):
        self.r.flushdb()
        gate._client = None
        self._settings.disable()


class AdmissionTests(_GateTestCase):
    def test_admits_until_capacity_then_blocks(self):
        self.assertTrue(gate.try_acquire("a", 12)[0])
        self.assertTrue(gate.try_acquire("b", 8)[0])
        admitted, in_flight, cap = gate.try_acquire("c", 1)
        self.assertFalse(admitted)
        self.assertEqual((in_flight, cap), (20, 20))

        gate.release("a")
        self.assertTrue(gate.try_acquire("c", 1)[0])
        self.assertEqual(gate.pages_in_flight(), 9)

    def test_fifo_head_blocks_smaller_documents_behind_it(self):
        # A 15-page document waiting first must not be overtaken by 2-page ones
        # that would fit — otherwise a steady stream of small docs starves it.
        self.assertTrue(gate.try_acquire("running", 10)[0])
        self.assertFalse(gate.try_acquire("big", 15)[0])
        self.assertFalse(gate.try_acquire("small", 2)[0])

        gate.release("running")
        self.assertFalse(gate.try_acquire("small", 2)[0])
        self.assertTrue(gate.try_acquire("big", 15)[0])
        self.assertTrue(gate.try_acquire("small", 2)[0])

    @override_settings(POLICY_EXTRACT_GATE_BACKFILL_SECONDS=30)
    def test_small_document_backfills_past_a_fresh_head(self):
        # The free pages a waiting 15-page head leaves are used by a doc that fits.
        self.assertTrue(gate.try_acquire("running", 10)[0])
        self.assertFalse(gate.try_acquire("big", 15)[0])
        self.assertTrue(gate.try_acquire("small", 8)[0])
        # ...but only into pages free now: this one doesn't fit and must wait.
        self.assertFalse(gate.try_acquire("medium", 5)[0])

    @override_settings(POLICY_EXTRACT_GATE_BACKFILL_SECONDS=30)
    def test_backfill_stops_once_the_head_has_waited_its_window(self):
        # The starvation bound: after the window, the line is strict FIFO again.
        self.assertTrue(gate.try_acquire("running", 10)[0])
        self.assertFalse(gate.try_acquire("big", 15)[0])
        self.r.hset(gate.KEY_SINCE, "big", 0)
        self.assertFalse(gate.try_acquire("small", 2)[0])
        gate.release("running")
        self.assertTrue(gate.try_acquire("big", 15)[0])

    def test_waiting_pages_are_tracked_until_admitted_or_gone(self):
        gate.try_acquire("running", 20)
        gate.try_acquire("w1", 7)
        gate.try_acquire("w2", 4)
        self.assertEqual(gate.waiting(), (2, 11))
        gate.leave_queue("w2")
        gate.release("running")
        gate.try_acquire("w1", 7)
        self.assertEqual(gate.waiting(), (0, 0))

    def test_document_larger_than_capacity_is_admitted_alone(self):
        self.assertTrue(gate.try_acquire("a", 5)[0])
        self.assertFalse(gate.try_acquire("huge", 50)[0])
        gate.release("a")
        self.assertTrue(gate.try_acquire("huge", 50)[0])
        self.assertFalse(gate.try_acquire("next", 1)[0])

    def test_capacity_key_overrides_env_and_reserve_is_held_back(self):
        self.r.set(gate.CAPACITY_KEY, 40)
        self.assertEqual(gate.capacity(), 40)
        self.assertTrue(gate.try_acquire("a", 35)[0])
        with override_settings(POLICY_EXTRACT_GATE_RESERVE_PAGES=10):
            self.assertEqual(gate.try_acquire("b", 1)[2], 30)
            self.assertFalse(gate.try_acquire("b", 1)[0])

    def test_expired_reservation_is_reaped(self):
        # A SIGKILLed child never releases; its TTL must free the pages.
        self.assertTrue(gate.try_acquire("dead", 20)[0])
        self.r.zadd(gate.KEY_RES, {"dead": 0})
        self.assertTrue(gate.try_acquire("next", 20)[0])
        self.assertIsNone(self.r.hget(gate.KEY_PAGES, "dead"))

    def test_lease_renewal_extends_only_live_reservations(self):
        gate.try_acquire("a", 5)
        before = self.r.zscore(gate.KEY_RES, "a")
        self.r.zadd(gate.KEY_RES, {"a": before - 30_000})
        self.assertTrue(gate.renew("a"))
        self.assertGreaterEqual(self.r.zscore(gate.KEY_RES, "a"), before)
        gate.release("a")
        self.assertFalse(gate.renew("a"))
        self.assertIsNone(self.r.zscore(gate.KEY_RES, "a"))

    def test_abandoned_ticket_drops_off_the_line(self):
        # A waiter killed mid-wait stops polling; it must not block the head forever.
        self.assertTrue(gate.try_acquire("running", 20)[0])
        self.assertFalse(gate.try_acquire("ghost", 5)[0])
        self.assertFalse(gate.try_acquire("live", 5)[0])
        gate.release("running")
        self.r.zadd(gate.KEY_HB, {"ghost": 0})
        self.assertTrue(gate.try_acquire("live", 5)[0])

    def test_release_counts_done_pages(self):
        gate.try_acquire("a", 7)
        gate.release("a")
        gate.release("a")  # idempotent
        stats = self.r.hgetall(gate.KEY_STATS)
        self.assertEqual(stats["pages_admitted"], "7")
        self.assertEqual(stats["docs_admitted"], "1")
        self.assertEqual(stats["pages_done"], "7")


class PageGateContextTests(_GateTestCase):
    def test_holds_only_inside_the_block(self):
        with gate.page_gate(6):
            self.assertEqual(gate.pages_in_flight(), 6)
        self.assertEqual(gate.pages_in_flight(), 0)

    def test_lease_is_kept_alive_while_the_call_runs(self):
        import time
        from unittest.mock import patch

        with patch.object(gate, "_RENEW_EVERY_S", 0.05):
            with gate.page_gate(4):
                token = self.r.hkeys(gate.KEY_PAGES)[0]
                self.r.zadd(gate.KEY_RES, {token: 0})  # about to expire
                time.sleep(0.3)
                self.assertGreater(self.r.zscore(gate.KEY_RES, token), 0)
        self.assertEqual(gate.pages_in_flight(), 0)

    def test_releases_when_the_call_raises(self):
        with self.assertRaises(RuntimeError):
            with gate.page_gate(6):
                raise RuntimeError("mineru 502")
        self.assertEqual(gate.pages_in_flight(), 0)

    def test_wait_timeout_is_retryable_and_leaves_the_line(self):
        gate.try_acquire("running", 20)
        with self.assertRaises(GateWaitTimeout) as ctx:
            with gate.page_gate(5):
                self.fail("must not be admitted")
        self.assertTrue(is_retryable(ctx.exception))
        self.assertEqual(self.r.zcard(gate.KEY_QUEUE), 0)

    def test_overloads_and_refusals_are_counted(self):
        import requests

        gate.try_acquire("running", 20)
        self.assertFalse(gate.try_acquire("waiting", 5)[0])
        gate.leave_queue("waiting")
        gate.release("running")
        with gate.page_gate(3) as call:
            call.status = 503
        with self.assertRaises(requests.Timeout):
            with gate.page_gate(3):
                raise requests.Timeout()
        with gate.page_gate(3) as call:
            call.status = 200
        stats = self.r.hgetall(gate.KEY_STATS)
        self.assertEqual(stats["overload"], "2")
        self.assertGreaterEqual(int(stats["refused"]), 1)

    def test_flag_off_never_waits_but_still_counts(self):
        gate.try_acquire("running", 20)
        with override_settings(POLICY_EXTRACT_PAGE_GATE=False):
            with gate.page_gate(5):
                self.assertEqual(gate.pages_in_flight(), 20)
        stats = self.r.hgetall(gate.KEY_STATS)
        self.assertEqual(stats["pages_admitted"], "25")
        self.assertEqual(stats["pages_done"], "5")

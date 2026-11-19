import asyncio
import os
import unittest
import uuid

from app.connectors import crypto
from app.connectors.base import ConnectionStatus, Event, TokenSet, dedup_key
from app.connectors.errors import (
    ConnectionRevoked,
    ConnectorError,
    PollInProgress,
    RateLimited,
    ReauthRequired,
)
from app.connectors.gmail.connector import GmailConnector
from app.connectors.http import HttpResponse
from app.connectors.ingestion import ConnectionState, backoff_delay, poll_connection
from app.connectors.testing.fake import FakeConnector
from app.connectors.testing.memory_repo import InMemoryConnectionRepo

run = asyncio.run
REDIRECT = "http://localhost:5173/cb"


class FakeHttp:
    """Routes by (method, url-suffix); handler(params, form) -> HttpResponse."""

    def __init__(self):
        self.routes, self.calls = [], []

    def on(self, method, suffix, handler):
        self.routes.append((method, suffix, handler))

    async def request(self, method, url, *, headers=None, params=None, form=None):
        self.calls.append((method, url, params, form))
        for m, suffix, h in self.routes:
            if m == method and url.endswith(suffix):
                return h(params or {}, form or {}) if callable(h) else h
        return HttpResponse(500)


def msg(mid, ts="1700000000000"):
    return HttpResponse(200, {
        "id": mid, "threadId": "t" + mid, "labelIds": ["INBOX"], "snippet": "hi", "internalDate": ts,
        "payload": {"headers": [
            {"name": "From", "value": "a@x.com"}, {"name": "Subject", "value": "S"},
            {"name": "X-Secret", "value": "drop-me"}]},
    })


def gmail(http):
    return GmailConnector(http, "cid", "secret", now=lambda: 1000.0)


TOK = TokenSet("acc", "ref", None)


class GmailTests(unittest.TestCase):
    def test_exchange_code_ok_and_params(self):
        h = FakeHttp()
        h.on("POST", "/token", HttpResponse(200, {"access_token": "a", "refresh_token": "r", "expires_in": 3600}))
        t = run(gmail(h).exchange_code("code", "ver", REDIRECT))
        self.assertEqual((t.access_token, t.refresh_token, t.expires_at), ("a", "r", 4600.0))
        form = h.calls[0][3]
        self.assertEqual(form["code_verifier"], "ver")
        self.assertEqual(form["grant_type"], "authorization_code")

    def test_exchange_without_refresh_token_fails(self):
        h = FakeHttp()
        h.on("POST", "/token", HttpResponse(200, {"access_token": "a"}))
        with self.assertRaises(ConnectorError):
            run(gmail(h).exchange_code("c", "v", REDIRECT))

    def test_exchange_failure_hides_body(self):
        h = FakeHttp()
        h.on("POST", "/token", HttpResponse(400, {"error": "secret-detail"}))
        with self.assertRaises(ConnectorError) as cm:
            run(gmail(h).exchange_code("c", "v", REDIRECT))
        self.assertNotIn("secret-detail", str(cm.exception))

    def test_refresh_keeps_refresh_token(self):
        h = FakeHttp()
        h.on("POST", "/token", HttpResponse(200, {"access_token": "new", "expires_in": 10}))
        t = run(gmail(h).refresh(TOK))
        self.assertEqual((t.access_token, t.refresh_token), ("new", "ref"))

    def test_refresh_invalid_grant(self):
        h = FakeHttp()
        h.on("POST", "/token", HttpResponse(400, {"error": "invalid_grant"}))
        with self.assertRaises(ReauthRequired):
            run(gmail(h).refresh(TOK))

    def test_initial_sync_captures_history_id_and_backfills(self):
        h = FakeHttp()
        h.on("GET", "/profile", HttpResponse(200, {"emailAddress": "A@X.com", "historyId": "500"}))
        h.on("GET", "/messages", HttpResponse(200, {"messages": [{"id": "m1"}, {"id": "m2"}]}))
        h.on("GET", "/messages/m1", msg("m1"))
        h.on("GET", "/messages/m2", msg("m2"))
        b = run(gmail(h).fetch_events(TOK, None))
        self.assertEqual(b.next_cursor, "500")
        self.assertEqual([e.external_id for e in b.events], ["m1", "m2"])
        self.assertEqual(b.events[0].payload["headers"], {"from": "a@x.com", "subject": "S"})
        self.assertTrue(b.events[0].occurred_at.startswith("2023-11-14"))

    def test_incremental_dedups_within_batch_and_skips_deleted(self):
        h = FakeHttp()
        h.on("GET", "/history", HttpResponse(200, {"historyId": "700", "history": [
            {"messagesAdded": [{"message": {"id": "m1"}}, {"message": {"id": "m2"}}]},
            {"messagesAdded": [{"message": {"id": "m1"}}]}]}))
        h.on("GET", "/messages/m1", msg("m1"))
        h.on("GET", "/messages/m2", HttpResponse(404))
        b = run(gmail(h).fetch_events(TOK, "600"))
        self.assertEqual([e.external_id for e in b.events], ["m1"])
        self.assertEqual(b.next_cursor, "700")

    def test_incremental_pagination(self):
        h = FakeHttp()
        pages = iter([
            HttpResponse(200, {"historyId": "650", "nextPageToken": "p2",
                               "history": [{"messagesAdded": [{"message": {"id": "m1"}}]}]}),
            HttpResponse(200, {"historyId": "700", "history": [{"messagesAdded": [{"message": {"id": "m2"}}]}]}),
        ])
        h.on("GET", "/history", lambda p, f: next(pages))
        h.on("GET", "/messages/m1", msg("m1"))
        h.on("GET", "/messages/m2", msg("m2"))
        b = run(gmail(h).fetch_events(TOK, "600"))
        self.assertEqual(len(b.events), 2)
        self.assertEqual(b.next_cursor, "700")

    def test_history_404_requests_reset(self):
        h = FakeHttp()
        h.on("GET", "/history", HttpResponse(404))
        b = run(gmail(h).fetch_events(TOK, "1"))
        self.assertTrue(b.reset_required)

    def test_rate_limit_mapping(self):
        h = FakeHttp()
        h.on("GET", "/history", HttpResponse(429, {}, {"Retry-After": "30"}))
        with self.assertRaises(RateLimited) as cm:
            run(gmail(h).fetch_events(TOK, "1"))
        self.assertEqual(cm.exception.retry_after, 30.0)

    def test_health_mapping(self):
        h = FakeHttp()
        h.on("GET", "/profile", HttpResponse(401))
        self.assertEqual(run(gmail(h).health(TOK)).status, ConnectionStatus.NEEDS_REAUTH)


class IngestionTests(unittest.TestCase):
    def setUp(self):
        self.kr = crypto.KeyRing.from_dict({"k1": os.urandom(32)}, "k1")
        self.repo = InMemoryConnectionRepo()
        self.t, self.cid = str(uuid.uuid4()), str(uuid.uuid4())
        self.conn = FakeConnector()
        self.conn.pending_events = []
        self.put(TokenSet("acc", "ref", None))

    def put(self, tokens, key_ring=None, status=ConnectionStatus.CONNECTED, cursor=None):
        kr = key_ring or self.kr
        ct, kid = crypto.encrypt(tokens.to_dict(), tenant_id=self.t, connection_id=self.cid, keyring=kr)
        self.repo.add(ConnectionState(self.cid, self.t, "fake", status, ct, kid, cursor))

    def poll(self, **kw):
        return run(poll_connection(self.repo, self.conn, self.kr, self.cid, **kw))

    def test_happy_path_and_idempotent(self):
        self.conn.pending_events = [Event("m1", "email.received"), Event("m2", "email.received")]
        r = self.poll()
        self.assertEqual((r.status, r.fetched, r.inserted), (ConnectionStatus.CONNECTED, 2, 2))
        self.assertEqual(self.repo.conns[self.cid].sync_cursor, "2")
        r2 = self.poll()  # same events again -> dedup
        self.assertEqual((r2.fetched, r2.inserted), (2, 0))
        self.assertEqual(len(self.repo.events), 2)

    def test_dedup_is_tenant_scoped(self):
        k = dedup_key(self.cid, "fake", "m1")
        self.assertNotEqual(k, dedup_key(str(uuid.uuid4()), "fake", "m1"))

    def test_crash_before_commit_keeps_cursor(self):
        self.conn.pending_events = [Event("m1", "e")]
        self.repo.fail_commit = True
        with self.assertRaises(RuntimeError):
            self.poll()
        self.assertIsNone(self.repo.conns[self.cid].sync_cursor)
        self.assertEqual(len(self.repo.events), 0)
        self.repo.fail_commit = False
        self.assertEqual(self.poll().inserted, 1)  # retried safely

    def test_lock_blocks_concurrent_poll(self):
        self.repo.locked.add(self.cid)
        with self.assertRaises(PollInProgress):
            self.poll()

    def test_revoked_rejected(self):
        self.put(TokenSet("a", "r"), status=ConnectionStatus.REVOKED)
        with self.assertRaises(ConnectionRevoked):
            self.poll()

    def test_expired_token_refreshed_and_persisted(self):
        self.put(TokenSet("old", "ref", 1000.0))
        r = self.poll(now=lambda: 990.0)  # within 60s skew
        self.assertEqual(r.status, ConnectionStatus.CONNECTED)
        c = self.repo.conns[self.cid]
        saved = crypto.decrypt_json(c.credentials_ciphertext, c.credentials_key_id,
                                    tenant_id=self.t, connection_id=self.cid, keyring=self.kr)
        self.assertEqual(saved["access_token"], "old+")

    def test_reauth_sets_needs_reauth(self):
        self.put(TokenSet("old", "ref", 1000.0))
        self.conn.reject_refresh = True
        try:
            r = self.poll(now=lambda: 990.0)
        finally:
            self.conn.reject_refresh = False
        self.assertEqual(r.status, ConnectionStatus.NEEDS_REAUTH)
        self.assertEqual(self.repo.conns[self.cid].status, ConnectionStatus.NEEDS_REAUTH)

    def test_cursor_expired_triggers_full_resync(self):
        self.put(TokenSet("a", "r"), cursor="5")
        self.conn.expired_cursor = True
        self.conn.pending_events = [Event("m9", "e")]
        # fake raises CursorExpired only when a cursor is given; resync passes None
        try:
            r = self.poll()
        finally:
            self.conn.expired_cursor = False
        self.assertTrue(r.full_resync)
        self.assertEqual(r.inserted, 1)

    def test_lazy_rotation(self):
        old_kr = crypto.KeyRing.from_dict({"k1": self.kr.keys["k1"]}, "k1")
        new_kr = crypto.KeyRing.from_dict({**old_kr.keys, "k2": os.urandom(32)}, "k2")
        self.put(TokenSet("a", "r"), key_ring=old_kr)
        run(poll_connection(self.repo, self.conn, new_kr, self.cid))
        self.assertEqual(self.repo.conns[self.cid].credentials_key_id, "k2")

    def test_backoff_bounds(self):
        self.assertEqual(backoff_delay(0, rng=lambda: 1.0), 2.0)
        self.assertEqual(backoff_delay(20, rng=lambda: 1.0), 900.0)
        self.assertEqual(backoff_delay(3, rng=lambda: 0.0), 0.0)


if __name__ == "__main__":
    unittest.main()

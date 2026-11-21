import asyncio
import os
import unittest
from urllib.parse import parse_qs, urlparse

from app.connectors import crypto
from app.connectors.base import ConnectionStatus, Event
from app.connectors.errors import AccountAlreadyBound, Forbidden, InvalidOAuthState, NotFound, PollInProgress
from app.connectors.oauth import InMemoryOAuthStateStore
from app.connectors.service import (
    CONNECTIONS_MANAGE,
    CONNECTIONS_READ,
    Actor,
    ConnectionService,
)
from app.connectors.testing.fake import FakeConnector
from app.connectors.testing.memory_repo import InMemoryConnectionRepo

run = asyncio.run
ADMIN = Actor("T1", "U1", frozenset({CONNECTIONS_READ, CONNECTIONS_MANAGE}))
MEMBER = Actor("T1", "U2", frozenset({CONNECTIONS_READ}))
OTHER_TENANT = Actor("T2", "U9", frozenset({CONNECTIONS_READ, CONNECTIONS_MANAGE}))


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.audit_log = []
        self.repo = InMemoryConnectionRepo()
        self.kr = crypto.KeyRing.from_dict({"k1": os.urandom(32)}, "k1")
        self.fake = FakeConnector()
        self.fake.pending_events = []
        self.svc = ConnectionService(
            repo=self.repo, state_store=InMemoryOAuthStateStore(), keyring=self.kr,
            connector_factory=lambda p: self.fake if p == "fake" else (_ for _ in ()).throw(KeyError(p)),
            state_secret="s", redirect_uri="http://x/cb",
            audit=lambda *a: self.audit_log.append(a),
        )

    def connect(self, actor=ADMIN, code="c1"):
        url = run(self.svc.start_oauth(actor, "fake"))
        state = parse_qs(urlparse(url).query)["state"][0]
        return run(self.svc.finish_oauth(code, state))

    def test_connect_encrypts_and_audits(self):
        v = self.connect()
        self.assertEqual((v.provider, v.status), ("fake", ConnectionStatus.CONNECTED))
        st = self.repo.conns[v.id]
        self.assertNotIn("fake-access", st.credentials_ciphertext)
        tokens = crypto.decrypt_json(st.credentials_ciphertext, st.credentials_key_id,
                                     tenant_id="T1", connection_id=v.id, keyring=self.kr)
        self.assertTrue(tokens["access_token"].startswith("fake-access"))
        action, tenant, user, payload = self.audit_log[-1]
        self.assertEqual((action, tenant, user), ("connection_created", "T1", "U1"))
        self.assertNotIn("token", str(payload).lower())

    def test_reconnect_updates_same_row_and_keeps_cursor(self):
        v1 = self.connect()
        self.repo.conns[v1.id].sync_cursor = "42"
        v2 = self.connect(code="c2")
        self.assertEqual(v1.id, v2.id)
        self.assertEqual(len(self.repo.conns), 1)
        self.assertEqual(self.repo.conns[v1.id].sync_cursor, "42")
        self.assertEqual(self.audit_log[-1][0], "connection_updated")

    def test_member_cannot_start_or_revoke_or_poll(self):
        v = self.connect()
        with self.assertRaises(Forbidden):
            run(self.svc.start_oauth(MEMBER, "fake"))
        with self.assertRaises(Forbidden):
            run(self.svc.revoke(MEMBER, v.id))
        with self.assertRaises(Forbidden):
            run(self.svc.poll(MEMBER, v.id))
        self.assertEqual(len(run(self.svc.list(MEMBER))), 1)  # read is allowed

    def test_tenant_isolation(self):
        v = self.connect()
        self.assertEqual(run(self.svc.list(OTHER_TENANT)), [])
        for call in (self.svc.health, self.svc.poll, self.svc.revoke):
            with self.assertRaises(NotFound):
                run(call(OTHER_TENANT, v.id))

    def test_callback_replay_rejected(self):
        url = run(self.svc.start_oauth(ADMIN, "fake"))
        state = parse_qs(urlparse(url).query)["state"][0]
        run(self.svc.finish_oauth("c", state))
        with self.assertRaises(InvalidOAuthState):
            run(self.svc.finish_oauth("c", state))

    def test_poll_via_service(self):
        v = self.connect()
        self.fake.pending_events = [Event("m1", "email.received")]
        r = run(self.svc.poll(ADMIN, v.id))
        self.assertEqual((r.status, r.inserted), (ConnectionStatus.CONNECTED, 1))
        self.assertEqual(self.audit_log[-1][0], "connection_polled")

    def test_poll_conflict(self):
        v = self.connect()
        self.repo.locked.add(v.id)
        with self.assertRaises(PollInProgress):
            run(self.svc.poll(ADMIN, v.id))

    def test_revoke_erases_credentials_keeps_row_and_is_idempotent(self):
        v = self.connect()
        run(self.svc.revoke(ADMIN, v.id))
        st = self.repo.conns[v.id]
        self.assertEqual(st.status, ConnectionStatus.REVOKED)
        self.assertIsNone(st.credentials_ciphertext)
        self.assertTrue(self.fake.revoked)
        n = len(self.audit_log)
        run(self.svc.revoke(ADMIN, v.id))
        self.assertEqual(len(self.audit_log), n)  # no duplicate audit

    def test_reconnect_after_revoke_reactivates(self):
        v = self.connect()
        run(self.svc.revoke(ADMIN, v.id))
        v2 = self.connect(code="c3")
        self.assertEqual(v2.id, v.id)
        self.assertEqual(v2.status, ConnectionStatus.CONNECTED)
        self.assertIsNotNone(self.repo.conns[v.id].credentials_ciphertext)

    def test_health_view_has_no_credentials(self):
        v = self.connect()
        h = run(self.svc.health(MEMBER, v.id))
        self.assertFalse(hasattr(h, "credentials_ciphertext"))

    def test_cross_department_account_conflict_and_reconnect_rules(self):
        v1 = run(
            self.svc.direct_connect(
                ADMIN, "gmail", "shared@acme.com", {"access_token": "tok1"}, department_id="dept-a"
            )
        )
        self.assertEqual(getattr(self.repo.conns[v1.id], "department_id", None), "dept-a")

        # Connecting the same external account to another department raises AccountAlreadyBound (409)
        with self.assertRaises(AccountAlreadyBound):
            run(
                self.svc.direct_connect(
                    ADMIN, "gmail", "shared@acme.com", {"access_token": "tok2"}, department_id="dept-b"
                )
            )
        # No silent move occurred
        self.assertEqual(getattr(self.repo.conns[v1.id], "department_id", None), "dept-a")

        # Reconnecting in the SAME department updates credentials in place
        v2 = run(
            self.svc.direct_connect(
                ADMIN, "gmail", "shared@acme.com", {"access_token": "tok3"}, department_id="dept-a"
            )
        )
        self.assertEqual(v2.id, v1.id)

        # Revoked account is free to be connected to another department
        run(self.svc.revoke(ADMIN, v1.id))
        v3 = run(
            self.svc.direct_connect(
                ADMIN, "gmail", "shared@acme.com", {"access_token": "tok4"}, department_id="dept-b"
            )
        )
        self.assertEqual(getattr(self.repo.conns[v3.id], "department_id", None), "dept-b")


if __name__ == "__main__":
    unittest.main()

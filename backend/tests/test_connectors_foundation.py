import asyncio
import base64
import os
import unittest
import uuid
from urllib.parse import parse_qs, urlparse

from app.connectors import crypto, oauth, registry
from app.connectors.base import TokenSet, dedup_key
from app.connectors.errors import CryptoError, InvalidOAuthState, ReauthRequired, UnknownProvider
from app.connectors.testing.fake import FakeConnector

run = asyncio.run
SECRET = "test-state-secret"
REDIRECT = "http://localhost:5173/connections/oauth/callback"


def ring(current="k1", extra=("k1",)):
    return crypto.KeyRing.from_dict({k: os.urandom(32) for k in extra}, current)


class CryptoTests(unittest.TestCase):
    def setUp(self):
        self.kr = ring()
        self.t, self.c = uuid.uuid4(), uuid.uuid4()

    def enc(self, payload=None):
        return crypto.encrypt(payload or {"access_token": "a", "refresh_token": "r"},
                              tenant_id=self.t, connection_id=self.c, keyring=self.kr)

    def test_roundtrip(self):
        ct, kid = self.enc()
        out = crypto.decrypt_json(ct, kid, tenant_id=self.t, connection_id=self.c, keyring=self.kr)
        self.assertEqual(out["refresh_token"], "r")

    def test_nonce_unique(self):
        self.assertNotEqual(self.enc()[0], self.enc()[0])

    def test_wrong_tenant_fails(self):
        ct, kid = self.enc()
        with self.assertRaises(CryptoError):
            crypto.decrypt_bytes(ct, kid, tenant_id=uuid.uuid4(), connection_id=self.c, keyring=self.kr)

    def test_wrong_connection_fails(self):
        ct, kid = self.enc()
        with self.assertRaises(CryptoError):
            crypto.decrypt_bytes(ct, kid, tenant_id=self.t, connection_id=uuid.uuid4(), keyring=self.kr)

    def test_tamper_fails(self):
        ct, kid = self.enc()
        raw = bytearray(base64.b64decode(ct))
        raw[-1] ^= 1
        with self.assertRaises(CryptoError):
            crypto.decrypt_bytes(base64.b64encode(bytes(raw)).decode(), kid,
                                 tenant_id=self.t, connection_id=self.c, keyring=self.kr)

    def test_bad_version_and_garbage(self):
        for bad in ("!!!", base64.b64encode(b"\x09" + b"x" * 40).decode()):
            with self.assertRaises(CryptoError):
                crypto.decrypt_bytes(bad, "k1", tenant_id=self.t, connection_id=self.c, keyring=self.kr)

    def test_rotation(self):
        old = crypto.KeyRing.from_dict({"k1": os.urandom(32)}, "k1")
        ct, kid = crypto.encrypt({"a": 1}, tenant_id=self.t, connection_id=self.c, keyring=old)
        new = crypto.KeyRing.from_dict({**old.keys, "k2": os.urandom(32)}, "k2")
        self.assertTrue(crypto.needs_rotation(kid, new))
        ct2, kid2 = crypto.rotate(ct, kid, tenant_id=self.t, connection_id=self.c, keyring=new)
        self.assertEqual(kid2, "k2")
        self.assertEqual(crypto.decrypt_json(ct2, kid2, tenant_id=self.t, connection_id=self.c, keyring=new), {"a": 1})

    def test_keyring_validation(self):
        with self.assertRaises(CryptoError):
            crypto.KeyRing.from_dict({"k": b"short"}, "k")
        with self.assertRaises(CryptoError):
            crypto.KeyRing.from_dict({"k": os.urandom(32)}, "missing")
        with self.assertRaises(CryptoError):
            crypto.KeyRing.from_env({})

    def test_from_env(self):
        import json
        env = {"CONNECTOR_ENCRYPTION_KEYS": json.dumps({"k1": crypto.generate_key()}),
               "CONNECTOR_ENCRYPTION_CURRENT_KEY_ID": "k1"}
        self.assertEqual(crypto.KeyRing.from_env(env).current_key_id, "k1")


class OAuthTests(unittest.TestCase):
    def setUp(self):
        self.now = [1_000_000.0]
        self.clock = lambda: self.now[0]
        self.store = oauth.InMemoryOAuthStateStore(clock=self.clock)
        self.conn = FakeConnector()

    def start(self):
        url = run(oauth.start_authorization(
            self.conn, tenant_id="T1", user_id="U1", store=self.store,
            state_secret=SECRET, redirect_uri=REDIRECT, clock=self.clock))
        return parse_qs(urlparse(url).query)

    def finish(self, state, code="abc"):
        return run(oauth.complete_authorization(self.conn, code=code, state=state, store=self.store,
                                                state_secret=SECRET, redirect_uri=REDIRECT, clock=self.clock))

    def test_authorize_url_params(self):
        q = self.start()
        self.assertEqual(q["code_challenge_method"], ["S256"])
        self.assertEqual(q["access_type"], ["offline"])
        self.assertEqual(q["prompt"], ["consent"])
        self.assertEqual(q["redirect_uri"], [REDIRECT])

    def test_full_flow_and_pkce(self):
        q = self.start()
        res = self.finish(q["state"][0])
        self.assertEqual((res.tenant_id, res.user_id), ("T1", "U1"))
        self.assertEqual(res.account.external_account_id, "fake-user@example.com")
        self.assertEqual(oauth.code_challenge_s256(self.conn.last_code_verifier), q["code_challenge"][0])

    def test_replay_rejected(self):
        state = self.start()["state"][0]
        self.finish(state)
        with self.assertRaises(InvalidOAuthState):
            self.finish(state)

    def test_expired_rejected(self):
        state = self.start()["state"][0]
        self.now[0] += oauth.STATE_TTL_SECONDS + 1
        with self.assertRaises(InvalidOAuthState):
            self.finish(state)

    def test_tampered_and_wrong_secret(self):
        state = self.start()["state"][0]
        with self.assertRaises(InvalidOAuthState):
            self.finish(state[:-2] + "xx")
        with self.assertRaises(InvalidOAuthState):
            oauth.verify_state(state, secret="other")

    def test_provider_mismatch(self):
        nonce = "n1"
        run(self.store.save(oauth.StateRecord(nonce, "T", "U", "fake", "v", self.clock() + 100)))
        tok = oauth.sign_state(nonce=nonce, provider="gmail", secret=SECRET, exp=self.clock() + 100)
        with self.assertRaises(InvalidOAuthState):
            self.finish(tok)

    def test_unknown_nonce(self):
        tok = oauth.sign_state(nonce="nope", provider="fake", secret=SECRET, exp=self.clock() + 100)
        with self.assertRaises(InvalidOAuthState):
            self.finish(tok)


class ConnectorTests(unittest.TestCase):
    def test_registry(self):
        self.assertIs(registry.get_connector_class("fake"), FakeConnector)
        self.assertIn("fake", registry.list_providers())
        with self.assertRaises(UnknownProvider):
            registry.get_connector_class("nope")

    def test_refresh_reauth(self):
        c = FakeConnector()
        c.reject_refresh = True
        with self.assertRaises(ReauthRequired):
            run(c.refresh(TokenSet("a", "r")))

    def test_dedup_key_scoped_by_connection(self):
        a, b = uuid.uuid4(), uuid.uuid4()
        self.assertNotEqual(dedup_key(a, "gmail", "m1"), dedup_key(b, "gmail", "m1"))

    def test_tokenset_roundtrip(self):
        t = TokenSet("a", "r", 1.0, "s")
        self.assertEqual(TokenSet.from_dict(t.to_dict()), t)


if __name__ == "__main__":
    unittest.main()

import asyncio

import pytest

from app.connectors import registry
from app.connectors.base import ConnectionStatus, OAuth2Connector, TokenSet
from app.connectors.http import HttpResponse

EXPECTED_PROVIDERS = {
    "gmail",
    "google_calendar",
    "google_drive",
    "google_sheets",
    "hubspot",
    "slack",
    "stripe",
    "zoho_books",
}


class MockHttpClient:
    def __init__(self, responses=None):
        self.responses = responses or {}
        self.calls = []

    async def request(self, method, url, *, headers=None, params=None, form=None, json=None):
        self.calls.append({"method": method, "url": url, "headers": headers, "params": params, "form": form, "json": json})
        if url in self.responses:
            return self.responses[url]
        return HttpResponse(200, {"ok": True, "user": {"email": "test@opsmind.internal"}})


def test_all_8_providers_are_registered():
    import app.connectors  # noqa: F401
    registered = set(registry.list_providers()) - {"fake"}
    assert registered == EXPECTED_PROVIDERS


@pytest.mark.parametrize("provider", sorted(EXPECTED_PROVIDERS))
def test_connector_classes_are_oauth2_compatible(provider):
    import app.connectors  # noqa: F401
    cls = registry.get_connector_class(provider)
    assert issubclass(cls, OAuth2Connector)
    assert cls.auth_endpoint
    assert cls.token_endpoint


@pytest.mark.parametrize("provider", sorted(EXPECTED_PROVIDERS))
def test_authorize_url_generation(provider):
    import app.connectors  # noqa: F401
    cls = registry.get_connector_class(provider)
    http = MockHttpClient()
    conn = cls(http, "client_123", "secret_123")

    url = conn.authorize_url(state="signed_state_token", code_challenge="pkce_challenge", redirect_uri="http://localhost:8000/callback")
    assert "client_123" in url
    assert "signed_state_token" in url
    assert "pkce_challenge" in url


def test_slack_connector_health_and_account():
    from app.connectors.slack.connector import SlackConnector
    http = MockHttpClient({
        "https://slack.com/api/auth.test": HttpResponse(200, {"ok": True, "team": "Acme Corp", "user": "opsmind_bot"})
    })
    conn = SlackConnector(http, "slack_cid", "slack_sec")
    tokens = TokenSet(access_token="xoxb-mock-token")
    
    account_id = asyncio.run(conn.fetch_account_id(tokens))
    assert account_id == "Acme Corp (opsmind_bot)"

    health = asyncio.run(conn.health(tokens))
    assert health.status == ConnectionStatus.CONNECTED


def test_stripe_connector_health_and_account():
    from app.connectors.stripe.connector import StripeConnector
    http = MockHttpClient({
        "https://api.stripe.com/v1/account": HttpResponse(200, {"id": "acct_12345", "business_profile": {"name": "OpsMind Billing"}}),
        "https://api.stripe.com/v1/balance": HttpResponse(200, {"available": [{"amount": 1000, "currency": "usd"}]})
    })
    conn = StripeConnector(http, "stripe_cid", "stripe_sec")
    tokens = TokenSet(access_token="sk_test_mock")

    account_id = asyncio.run(conn.fetch_account_id(tokens))
    assert account_id == "acct_12345 (OpsMind Billing)"

    health = asyncio.run(conn.health(tokens))
    assert health.status == ConnectionStatus.CONNECTED

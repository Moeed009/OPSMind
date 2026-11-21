"""Optional live smoke check for Groq/Gemini providers when keys are configured in local .env.

Usage (from backend/):
    uv run python scripts/ai_smoke.py
"""
from __future__ import annotations

import asyncio
import uuid

from app.ai.services.dept_ai_service import AiContext, DepartmentAiService
from app.core.config import get_settings
from app.workflows.definition import AINode


async def main() -> None:
    settings = get_settings()
    groq_ok = bool(settings.groq_api_key and settings.groq_api_key.strip())
    gemini_ok = bool(settings.gemini_api_key and settings.gemini_api_key.strip())
    print(f"Providers configured -> groq={groq_ok}, gemini={gemini_ok}")
    if not (groq_ok or gemini_ok):
        print("No live AI keys configured; skipping live call.")
        return

    svc = DepartmentAiService()
    res = await svc.run(
        None,
        AiContext(tenant_id=uuid.uuid4()),
        "classify",
        {"subject": "Invoice #104", "body": "Please clarify why I was charged twice."},
        AINode(id="smoke", task="classify", labels=["billing", "bug", "query"], provider="auto"),
    )
    print(f"Result: status={res.status} provider={res.provider} model={res.model} latency_ms={res.latency_ms} output={res.output}")


if __name__ == "__main__":
    asyncio.run(main())

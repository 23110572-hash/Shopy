"""Strict OpenRouter structured-output adapter for Shopy shopping decisions."""

import json
from typing import Any, cast

import httpx

from backend.app.config import Settings


class LLMProviderError(RuntimeError):
    """Raised when a configured LLM provider cannot return valid structured data."""


class OpenRouterGateway:
    def __init__(self, settings: Settings) -> None:
        api_key, model = settings.require_openrouter()
        self._api_key = api_key
        self._model = model
        self._endpoint = f"{str(settings.openrouter_base_url).rstrip('/')}/chat/completions"
        self._referer = (
            str(settings.openrouter_http_referer) if settings.openrouter_http_referer else None
        )
        self._app_title = settings.openrouter_app_title

    async def parse_structured_intent(
        self,
        *,
        user_text: str,
        json_schema: dict[str, Any],
    ) -> dict[str, Any]:
        return await self._structured_completion(
            schema_name="shopping_intent",
            json_schema=json_schema,
            system_prompt=(
                "Extract only shopping-search intent from the user message. Return JSON matching "
                "the supplied schema. Do not invent products, prices, availability, payment "
                "status, or purchases. Every schema property must be present; use null only where "
                "the schema allows null."
            ),
            user_payload={"message": user_text},
            failure_label="intent parsing",
        )

    async def compare_products(
        self,
        *,
        user_text: str,
        intent: dict[str, Any],
        candidates: list[dict[str, Any]],
        json_schema: dict[str, Any],
    ) -> dict[str, Any]:
        return await self._structured_completion(
            schema_name="product_comparison",
            json_schema=json_schema,
            system_prompt=(
                "You are Shopy's product comparison engine. Compare the real candidate "
                "specifications, price, verified description, and inventory supplied in JSON. "
                "Select exactly one candidate whose role is primary. ranked_product_ids may "
                "contain only primary candidate IDs. An upsell must be another primary candidate "
                "and a cross-sell must have role complementary. Use null when no honest upsell or "
                "cross-sell exists. Never invent an ID, feature, price, sales metric, review, "
                "availability, payment, or order. Explain concrete trade-offs. Every schema "
                "property must be present."
            ),
            user_payload={
                "request": user_text,
                "intent": intent,
                "candidates": candidates,
            },
            failure_label="product comparison",
        )

    async def _structured_completion(
        self,
        *,
        schema_name: str,
        json_schema: dict[str, Any],
        system_prompt: str,
        user_payload: dict[str, Any],
        failure_label: str,
    ) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "X-Title": self._app_title,
        }
        if self._referer is not None:
            headers["HTTP-Referer"] = self._referer

        request_body: dict[str, Any] = {
            "model": self._model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": json.dumps(
                        user_payload,
                        separators=(",", ":"),
                        ensure_ascii=False,
                    ),
                },
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "strict": True,
                    "schema": json_schema,
                },
            },
        }

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0)) as client:
                response = await client.post(self._endpoint, headers=headers, json=request_body)
                response.raise_for_status()
                provider_payload: object = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise LLMProviderError(f"OpenRouter {failure_label} failed") from exc

        try:
            payload = cast(dict[str, Any], provider_payload)
            content: object = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMProviderError("OpenRouter returned an invalid response shape") from exc
        if not isinstance(content, str):
            raise LLMProviderError("OpenRouter returned non-text structured content")

        try:
            parsed: object = json.loads(content)
        except json.JSONDecodeError as exc:
            raise LLMProviderError("OpenRouter returned invalid JSON") from exc
        if not isinstance(parsed, dict) or not all(isinstance(key, str) for key in parsed):
            raise LLMProviderError("OpenRouter returned a non-object result")
        return {str(key): value for key, value in parsed.items()}

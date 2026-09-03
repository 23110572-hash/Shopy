"""Provider-neutral LLM port. Runtime implementations must be real providers."""

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class LLMGateway(Protocol):
    async def parse_structured_intent(
        self,
        *,
        user_text: str,
        json_schema: dict[str, Any],
    ) -> dict[str, Any]:
        """Parse user text without receiving payment, token, or database credentials."""
        ...

    async def compare_products(
        self,
        *,
        user_text: str,
        intent: dict[str, Any],
        candidates: list[dict[str, Any]],
        json_schema: dict[str, Any],
    ) -> dict[str, Any]:
        """Choose only from real, bounded candidate IDs supplied by the commerce core."""
        ...

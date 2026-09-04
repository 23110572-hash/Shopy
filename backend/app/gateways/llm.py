"""Provider-neutral LLM port for semantic shopping and grounded comparison."""

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class LLMGateway(Protocol):
    async def understand_request(
        self,
        *,
        user_text: str,
        conversation_context: dict[str, Any],
        catalogue_context: dict[str, Any],
        json_schema: dict[str, Any],
    ) -> dict[str, Any]:
        """Interpret one turn before catalogue retrieval, with no payment authority."""
        ...

    async def evaluate_catalogue(
        self,
        *,
        understanding: dict[str, Any],
        search_plan: dict[str, Any],
        candidates: list[dict[str, Any]],
        diagnostics: dict[str, Any],
        json_schema: dict[str, Any],
    ) -> dict[str, Any]:
        """Choose a bounded shortlist or a safe, non-broadening search refinement."""
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

"""
app/agents/explanation_agent.py
==================================
Business Insight Explanation Layer.

Transforms raw query results into natural language business insights
that a non-technical stakeholder can read and understand.

WHY a separate explanation agent?
------------------------------------
The SQL generation and explanation are fundamentally different tasks:
  - SQL generation: deterministic, requires schema precision, temp=0.0
  - Explanation:    requires synthesis and business language, temp=0.3

Different temperature, different system prompt, different few-shot examples.
Mixing them into one prompt would compromise both tasks.

This is the "explain like a senior analyst" layer that transforms:
    [{'seller_state': 'SP', 'total_revenue': 1523442.31}, ...]

Into:
    "São Paulo (SP) dominates seller revenue, generating R$1.52M across 847
    orders -- accounting for approximately 38% of total platform revenue.
    The top 3 states (SP, MG, PR) collectively represent 61% of all revenue,
    showing high geographic concentration."

Design Considerations
----------------------
1. We pass the ACTUAL DATA (first 10 rows) to the LLM so it can
   reference specific numbers. Vague explanations ("some sellers performed
   better") are useless. Specific ones ("SP generated R$1.52M") are valuable.

2. We ask for 2-3 sentences. Longer explanations dilute the insight.
   Enterprise analytics tools (Tableau, Looker) show 1-3 sentence summaries.

3. Temperature 0.3 (not 0.0): slight creativity helps produce natural
   business language. Fully deterministic explanations sound robotic.

4. Graceful degradation: if the LLM call fails, we return a simple
   auto-generated summary based on the row count and column names.
   The pipeline never fails completely due to the explanation layer.
"""

from app.core.config import settings
from app.core.logger import logger
from app.services.llm_service import LLMProvider
from app.services.prompt_service import PromptService
from app.schemas.query import QueryExecutionResult


class ExplanationAgent:
    """Generates natural-language business insights from query results."""

    def __init__(
        self,
        llm_provider: LLMProvider,
        prompt_service: PromptService,
    ) -> None:
        self._llm      = llm_provider
        self._prompter = prompt_service

    async def explain(
        self,
        question: str,
        sql: str,
        exec_result: QueryExecutionResult,
    ) -> str:
        """
        Generate a business insight explanation for the query results.

        Returns a plain-text explanation string (1-3 sentences).
        Falls back to a simple auto-generated summary if LLM fails.
        """
        if not exec_result.rows:
            return "The query returned no results. This may indicate no matching data for the given filters."

        messages = self._prompter.build_explanation_messages(
            question=question,
            sql=sql,
            rows=exec_result.rows,
            columns=exec_result.columns,
            row_count=exec_result.row_count,
        )

        try:
            response = await self._llm.complete(
                messages=messages,
                temperature=0.3,   # slightly creative for natural language
                max_tokens=256,    # insights should be concise
            )

            insight = response.content.strip()

            logger.info(
                "ExplanationAgent: insight generated | {n} chars | tokens={t}",
                n=len(insight),
                t=response.total_tokens,
            )

            return insight

        except Exception as e:
            logger.warning(
                "ExplanationAgent: LLM call failed ({e}), using fallback",
                e=str(e)
            )
            return self._fallback_explanation(question, exec_result)

    @staticmethod
    def _fallback_explanation(question: str, result: QueryExecutionResult) -> str:
        """
        Simple rule-based fallback when the LLM explanation fails.
        Ensures the API always returns a meaningful response.
        """
        if result.row_count == 0:
            return "No results found for the given question."

        col_summary = ", ".join(result.columns[:4])
        if len(result.columns) > 4:
            col_summary += f", and {len(result.columns) - 4} more fields"

        capped_note = f" (results capped at {result.row_count} rows)" if result.was_capped else ""

        return (
            f"Query executed successfully{capped_note}. "
            f"Returned {result.row_count} rows with fields: {col_summary}. "
            f"Results are ordered by the most relevant metric for your question."
        )

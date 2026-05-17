"""
app/services/prompt_service.py
================================
Dynamic prompt assembly service.

Responsibilities:
  1. Select the most relevant few-shot examples for the question
  2. Assemble the full prompt (system + user) ready for LLM submission
  3. Build retry/correction prompts
  4. Build insight explanation prompts

Few-Shot Selection Strategy
-----------------------------
We tag each example with a SQL pattern type and score the user's question
against each tag.  The top-matching examples are selected.

This "pattern-based retrieval" is a lightweight alternative to embedding-
based semantic search (RAG over examples).  It works well when:
  - The example library is small (<20 examples)
  - The query patterns are well-defined (analytics is predictable)
  - Latency matters (no extra embedding API call needed)

For larger example libraries, replace _select_examples() with a vector
similarity search over embedded examples.
"""

import re

from app.prompts.sql_prompt import (
    FEW_SHOT_EXAMPLES,
    FewShotExample,
    build_user_prompt,
    format_few_shot_block,
)
from app.prompts.retry_prompt import (
    build_retry_prompt,
    build_explanation_prompt,
)
from app.prompts.system_prompt import SQL_GENERATION_SYSTEM_PROMPT
from app.prompts.retry_prompt import RETRY_SYSTEM_PROMPT
from app.services.context_service import ContextPackage
from app.core.logger import logger


# ── Pattern detection for example selection ───────────────────────────────────

_PATTERN_SIGNALS: dict[str, re.Pattern] = {
    "ranking":     re.compile(r'\btop|highest|best|most|lowest|worst|rank\b', re.I),
    "timeseries":  re.compile(r'\bmonth|week|year|trend|over time|period\b', re.I),
    "aggregation": re.compile(r'\baverage|avg|mean|total|sum|count|how many\b', re.I),
    "filter":      re.compile(r'\bwhere|which|that|with|only|filter\b', re.I),
    "join":        re.compile(r'\bby seller|by product|by category|by customer|per \w+\b', re.I),
}

_N_EXAMPLES = 3  # Number of few-shot examples to include in each prompt


class PromptService:
    """Assembles production-ready prompts for every pipeline stage."""

    def build_sql_prompt(
        self,
        question: str,
        context: ContextPackage,
        max_rows: int = 50,
    ) -> list[dict[str, str]]:
        """
        Build the complete [system, user] message list for SQL generation.

        Returns the OpenAI messages format:
            [
                {"role": "system", "content": "..."},
                {"role": "user",   "content": "..."},
            ]

        WHY return a list of dicts instead of a single string?
          The OpenAI chat format is the universal LLM API format.
          Every provider (Grok, Together, Groq) accepts this same structure.
          Keeping system and user turns separate lets the provider apply
          role-specific processing (system prompt caching, attention weighting).
        """
        examples    = self._select_examples(question)
        few_shot_block = format_few_shot_block(examples)

        user_content = build_user_prompt(
            question=question,
            schema_context=context.schema_prompt,
            relationship_context=context.relationship_prompt,
            few_shot_block=few_shot_block,
            max_rows=max_rows,
        )

        logger.debug(
            "PromptService: SQL prompt built | examples={n} | patterns={p} | "
            "user_chars={c}",
            n=len(examples),
            p=[e.pattern for e in examples],
            c=len(user_content),
        )

        return [
            {"role": "system", "content": SQL_GENERATION_SYSTEM_PROMPT},
            {"role": "user",   "content": user_content},
        ]

    def build_retry_messages(
        self,
        question: str,
        failed_sql: str,
        error_message: str,
        context: ContextPackage,
        attempt_number: int,
    ) -> list[dict[str, str]]:
        """Build the correction prompt for a failed SQL attempt."""
        user_content = build_retry_prompt(
            question=question,
            failed_sql=failed_sql,
            error_message=error_message,
            schema_context=context.schema_prompt,
            attempt_number=attempt_number,
        )
        logger.debug(
            "PromptService: retry prompt built | attempt={a}", a=attempt_number
        )
        return [
            {"role": "system", "content": RETRY_SYSTEM_PROMPT},
            {"role": "user",   "content": user_content},
        ]

    def build_explanation_messages(
        self,
        question: str,
        sql: str,
        rows: list[dict],
        columns: list[str],
        row_count: int,
    ) -> list[dict[str, str]]:
        """Build the business insight prompt."""
        content = build_explanation_prompt(
            question=question,
            sql=sql,
            rows=rows,
            columns=columns,
            row_count=row_count,
        )
        return [
            {
                "role": "system",
                "content": (
                    "You are a senior business analyst. Provide concise, "
                    "data-driven insights. Use specific numbers from the results."
                ),
            },
            {"role": "user", "content": content},
        ]

    def _select_examples(self, question: str) -> list[FewShotExample]:
        """
        Select the N most relevant few-shot examples for the question.

        Algorithm:
          1. Score each example pattern against the question
          2. Sort by score descending
          3. Take top N, ensuring pattern diversity

        Why diversity matters:
          If all 3 selected examples are "ranking" type and the user asks
          a time-series question, the examples don't help calibrate the
          right SQL structure.  We pick at most 2 examples of the same
          pattern.
        """
        # Detect active patterns in the question
        active_patterns: set[str] = set()
        for pattern_name, regex in _PATTERN_SIGNALS.items():
            if regex.search(question):
                active_patterns.add(pattern_name)

        if not active_patterns:
            # No pattern detected -- return first N examples as default
            return FEW_SHOT_EXAMPLES[:_N_EXAMPLES]

        # Score examples: 2 pts if pattern matches, 1 pt otherwise (still useful)
        scored: list[tuple[int, FewShotExample]] = []
        for ex in FEW_SHOT_EXAMPLES:
            score = 2 if ex.pattern in active_patterns else 1
            scored.append((score, ex))

        scored.sort(key=lambda x: x[0], reverse=True)

        # Select top N with pattern diversity (max 2 of same pattern)
        selected: list[FewShotExample] = []
        pattern_count: dict[str, int] = {}
        for score, ex in scored:
            if len(selected) >= _N_EXAMPLES:
                break
            if pattern_count.get(ex.pattern, 0) < 2:
                selected.append(ex)
                pattern_count[ex.pattern] = pattern_count.get(ex.pattern, 0) + 1

        return selected

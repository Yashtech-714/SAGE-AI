"""
app/api/routes/examples.py
============================
GET /examples — sample business questions for demos and documentation.

WHY an examples endpoint?
----------------------------
1. Demo value: a stakeholder visiting the API docs can immediately
   see what kinds of questions the system handles.

2. Frontend integration: a Streamlit or React frontend can fetch
   /examples to populate a "Try these questions" panel.

3. Testing: the examples list serves as a regression test suite —
   every example should return a successful result.

4. Onboarding: new developers understand the system's capabilities
   from the examples, not from reading source code.
"""

from fastapi import APIRouter

from app.api.routes.query import EXAMPLE_QUESTIONS

router = APIRouter(tags=["Documentation"])


@router.get(
    "/examples",
    summary="Sample business questions",
    description=(
        "Returns a curated list of example business analytics questions "
        "that the AI engine can answer. Use these to explore the system's capabilities."
    ),
)
async def get_examples() -> dict:
    """Return example questions grouped by category."""
    # Group by category
    by_category: dict[str, list] = {}
    for ex in EXAMPLE_QUESTIONS:
        cat = ex["category"]
        by_category.setdefault(cat, [])
        by_category[cat].append({
            "question": ex["question"],
            "max_rows": ex["max_rows"],
            "complexity": ex["complexity"],
        })

    return {
        "total_examples": len(EXAMPLE_QUESTIONS),
        "categories": list(by_category.keys()),
        "examples_by_category": by_category,
        "all_questions": [ex["question"] for ex in EXAMPLE_QUESTIONS],
        "tip": "POST any of these questions to /query to see the AI in action.",
    }

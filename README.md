<div align="center">

# ⚡ NL2SQL — AI Analytics Engine

### Natural Language → SQL → Business Insights

**Ask any business question in plain English. Get real data, generated SQL, and AI-powered insights.**

[![FastAPI](https://img.shields.io/badge/FastAPI-0.111-009688?style=flat-square&logo=fastapi)](https://fastapi.tiangolo.com)
[![React](https://img.shields.io/badge/React-18.3-61DAFB?style=flat-square&logo=react)](https://react.dev)
[![Python](https://img.shields.io/badge/Python-3.12-3776AB?style=flat-square&logo=python)](https://python.org)
[![TailwindCSS](https://img.shields.io/badge/Tailwind-3.4-06B6D4?style=flat-square&logo=tailwindcss)](https://tailwindcss.com)
[![Groq](https://img.shields.io/badge/Groq-Llama--3.3--70b-F55036?style=flat-square)](https://groq.com)
[![SQLite](https://img.shields.io/badge/SQLite-Olist%20Dataset-003B57?style=flat-square&logo=sqlite)](https://kaggle.com/datasets/olistbr/brazilian-ecommerce)

</div>

---

## What is this?

NL2SQL is a **production-grade AI analytics assistant** built on the Olist Brazilian E-Commerce dataset (99,441 orders, 9 relational tables). It converts plain English business questions into optimized SQL, executes them, and returns structured results with AI-generated business insights.

**Example:**
> *"Which sellers generated the highest total revenue?"*

The system generates this SQL automatically:
```sql
SELECT s.seller_id, s.seller_city, s.seller_state,
       COUNT(DISTINCT o.order_id) AS total_orders,
       ROUND(SUM(oi.price + oi.freight_value), 2) AS total_revenue
FROM sellers s
JOIN order_items oi ON s.seller_id = oi.seller_id
JOIN orders o ON oi.order_id = o.order_id
WHERE o.order_status = 'delivered'
GROUP BY s.seller_id, s.seller_city, s.seller_state
ORDER BY total_revenue DESC
LIMIT 50
```

And returns: **results table + business insight + execution metrics**.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    React Frontend (Vite)                      │
│  QueryInput → LoadingState → SqlViewer → ResultsTable        │
│              InsightCard → MetricsPanel → QueryHistory        │
└────────────────────────┬────────────────────────────────────┘
                         │ HTTP (Axios + React Query)
                         ▼
┌─────────────────────────────────────────────────────────────┐
│                   FastAPI REST API                            │
│  POST /query  GET /health  GET /schema  GET /metrics         │
│  RequestTracingMiddleware → Structured Error Handlers         │
└────────────────────────┬────────────────────────────────────┘
                         │
          ┌──────────────┴──────────────┐
          ▼                             ▼
┌─────────────────┐          ┌──────────────────────┐
│  AI Pipeline    │          │  Schema Intelligence  │
│                 │          │                      │
│  ContextService │          │  SchemaService        │
│  (table select) │          │  (9 tables, FK map)  │
│       ↓         │          │  ValidatorService     │
│  PromptService  │          │  (6 safety layers)   │
│  (schema-aware) │          │  RelationshipService  │
│       ↓         │          │  (graph traversal)   │
│  LLMProvider    │          └──────────────────────┘
│  (Groq/OpenAI)  │
│       ↓         │
│  RetryAgent     │ ← feeds validation errors back to LLM
│  (self-correct) │
│       ↓         │
│  ExecutionSvc   │
│  (async SQLite) │
│       ↓         │
│ ExplanationAgent│
│  (business NL)  │
└─────────────────┘
```

---

## Tech Stack

### Backend
| Technology | Purpose |
|---|---|
| **FastAPI** | Async REST API with lifespan-managed services |
| **SQLAlchemy 2.0** | Async ORM with `aiosqlite` driver |
| **Pydantic v2** | Request/response validation and settings management |
| **OpenAI SDK** | Provider-agnostic LLM client (works with Groq, OpenAI, Grok, Together) |
| **Groq API** | Default LLM provider — Llama-3.3-70b at ~400 tokens/sec |
| **sqlparse** | SQL parsing and safety validation |
| **networkx** | Schema relationship graph for JOIN path traversal |

### Frontend
| Technology | Purpose |
|---|---|
| **React 18** | Component-based UI with hooks |
| **Vite 5** | Sub-400ms HMR dev server |
| **TailwindCSS 3** | Utility-first dark theme styling |
| **TanStack Query** | Schema/examples caching, async state management |
| **Axios** | HTTP client with interceptor-based error handling |

### Dataset
| Property | Value |
|---|---|
| Source | [Olist Brazilian E-Commerce](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce) |
| Size | 128 MB SQLite database |
| Orders | 99,441 real transactions |
| Date range | 2016 – 2018 |
| Tables | 9 (customers, orders, order_items, order_payments, order_reviews, products, sellers, geolocation, category_translation) |

---

## Key Features

- 🤖 **Provider-agnostic LLM** — swap Groq, OpenAI, Grok, Together via one env var
- 🔄 **Self-correcting retry agent** — feeds validation errors back to the LLM (reduces failure rate to ~1-2%)
- 🛡️ **6-layer SQL safety validator** — blocks injection, DDL, hallucinated tables/columns
- ⚡ **Token-efficient prompting** — keyword-to-table relevance mapping (~60% fewer tokens vs full-schema injection)
- 📊 **Live observability** — request IDs, latency tracking, token usage, retry metrics
- 🎨 **Enterprise dark UI** — SQL syntax highlighting, sortable tables, CSV export, query history

---

## Quick Start

### Prerequisites

- Python 3.12+
- Node.js 18+
- A [Groq API key](https://console.groq.com) (free tier works)
- `olist.db` in `datasets/` ([download from Kaggle](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce) and run the setup script)

### 1. Clone the repository

```bash
git clone https://github.com/YOUR_USERNAME/nl2sql-analytics.git
cd nl2sql-analytics
```

### 2. Set up the backend

```bash
# Create and activate virtual environment
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # Mac/Linux

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env and add your Groq API key:
# LLM_API_KEY=gsk_your_key_here
```

### 3. Set up the database

Download the Olist dataset from Kaggle and run:

```bash
# Place olist.db in datasets/ folder
# OR if you have the CSV files:
python scripts/setup_db.py
```

### 4. Start the backend

```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Visit **http://localhost:8000/docs** for the interactive API explorer.

### 5. Set up the frontend

```bash
cd frontend
npm install
npm run dev
```

Visit **http://localhost:5173** for the analytics workspace.

---

## API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/query` | Submit a natural language question → get SQL + results + insight |
| `GET` | `/health` | Live health check (DB, LLM, schema, config) |
| `GET` | `/schema` | Full database schema metadata |
| `GET` | `/schema/table/{name}` | Column details for a specific table |
| `GET` | `/metrics` | Query counts, success rate, latency, token usage |
| `GET` | `/examples` | 8 curated sample business questions |
| `GET` | `/docs` | Interactive Swagger UI |

### Example Request

```bash
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{"question": "Which sellers generated the highest revenue?", "max_rows": 10}'
```

### Example Response

```json
{
  "question": "Which sellers generated the highest revenue?",
  "sql": "SELECT s.seller_id, ROUND(SUM(oi.price), 2) AS revenue ...",
  "rows": [{ "seller_id": "abc123", "revenue": 247007.06 }],
  "columns": ["seller_id", "seller_city", "revenue"],
  "row_count": 10,
  "insight": "The top seller achieved R$247K revenue from 1,124 orders...",
  "execution_time_ms": 45.2,
  "attempts": 1,
  "total_tokens": 892,
  "context_tables": ["sellers", "order_items", "orders"],
  "success": true
}
```

---

## Project Structure

```
nl2sql-analytics/
├── app/
│   ├── agents/          # SQL generation, retry, explanation agents
│   ├── api/             # FastAPI routes, middleware, dependencies
│   ├── core/            # Config, logging, metrics, app lifecycle
│   ├── db/              # SQLAlchemy session and database init
│   ├── prompts/         # System and retry prompt templates
│   ├── schemas/         # Pydantic request/response models
│   └── services/        # LLM, schema, validation, execution services
├── frontend/
│   └── src/
│       ├── components/  # QueryInput, SqlViewer, ResultsTable, InsightCard...
│       ├── hooks/       # useQueryHistory
│       ├── pages/       # Dashboard
│       ├── services/    # Axios API client
│       └── utils/       # SQL highlighter, formatters
├── scripts/             # Test suites and DB utilities
├── datasets/            # olist.db (not committed — download separately)
├── main.py              # FastAPI application entry point
├── requirements.txt
├── .env.example         # Environment variable template
└── ENGINEERING.md       # Deep-dive architecture documentation
```

---

## Sample Questions to Try

| Question | Complexity |
|---|---|
| "Which sellers generated the highest total revenue?" | multi-join + aggregation |
| "Show monthly order count and revenue trends for 2018" | time series |
| "What is the average review score by product category?" | 3-table join |
| "Which customer states have the most orders?" | simple aggregation |
| "What is the average delivery time in days by seller state?" | date arithmetic |
| "Which payment methods are most popular?" | single-table aggregate |
| "Which product categories have the lowest review scores?" | multi-join |

---

## LLM Provider Switching

Switch providers by changing 4 lines in `.env`:

```bash
# Groq (default — fastest, free)
LLM_PROVIDER=groq
LLM_API_KEY=gsk_...
LLM_BASE_URL=https://api.groq.com/openai/v1
LLM_MODEL=llama-3.3-70b-versatile

# OpenAI
LLM_PROVIDER=openai
LLM_API_KEY=sk-...
LLM_BASE_URL=
LLM_MODEL=gpt-4o-mini

# Grok / X.AI
LLM_PROVIDER=grok
LLM_API_KEY=xai-...
LLM_BASE_URL=https://api.x.ai/v1
LLM_MODEL=grok-3-mini
```

---

## Running Tests

```bash
# Phase 2: Schema introspection + SQL validator
python -m scripts.test_phase2

# Phase 3: LLM pipeline + retry agent
python -m scripts.test_phase3

# Phase 4: FastAPI endpoints (mock LLM, no API key needed)
python -m scripts.test_phase4
```

---

<div align="center">

Built with ⚡ FastAPI · React · Groq · SQLAlchemy · TailwindCSS

</div>

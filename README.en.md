# CampusBridge

Optional CPU Dense and RRF Hybrid retrieval are available through the existing KnowledgePort. On 72 frozen queries, BM25 / Dense / Hybrid chunk Recall@3 is 76.79% / 91.07% / 89.29%; on 32 held-out queries it is 83.33% / 85.42% / 89.58%. BM25 remains the dependency-free default. [Implementation and reproduction](docs/RAG_STAGE_B.md) · [Complete retrieval results](experiments/rag/stage-b/20261015-v1/analysis/COMPARISON.md).

[![CampusBridge CI](https://github.com/chen0206a/CampusBridge-Agent/actions/workflows/ci.yml/badge.svg)](https://github.com/chen0206a/CampusBridge-Agent/actions/workflows/ci.yml)

**Original student-support agents with dynamic planning, evidence arbitration, reviewed delivery, and human follow-up.** [中文](README.md)

CampusBridge connects student conversations, cited campus knowledge, and a counselor case workspace in one working application. The backend uses Python/FastAPI/Pydantic/SQLAlchemy/SQLite; the web client uses React/Vite/TypeScript. Original code and assets are MIT licensed.

## Architecture

![Architecture](docs/assets/campusbridge-stage6.png)

- **Typed task DAG:** TaskPlanner selects an execution path using the request, history and structured Intent/Risk outputs. The scheduler enforces dependencies, finite budgets and cancellation.
- **Bounded concurrency:** independent Intent/Risk tasks run concurrently with separate request contexts; a coordinator merges their results. History-dependent requests retain a dependency. Database Sessions are short and are never shared across concurrent agents.
- **Arbitration:** high-risk priority, explicit human-support requests at LOW severity, partial failures and irrelevant evidence are resolved by server policies with traceable evidence hashes.
- **Reviewed delivery:** complete candidates are reviewed and bound by candidate/content/citation hashes. Rejected text is never streamed. One regeneration is allowed before a fixed safe fallback.
- **Grounded RAG:** original campus documents, versioned imports, SQLite FTS5/BM25, deterministic citation allowlists and inspectable source details.
- **Human workflow:** persist attention events/cases before generation; enforce OPEN → CLAIMED → IN_PROGRESS → CLOSED with authorization, optimistic versions, idempotency and audit history.

![Student chat](docs/assets/showcase/student-chat.jpg)

![Counselor workspace](docs/assets/showcase/counselor-workbench.png)

[More screenshots](docs/PROJECT_SHOWCASE.md) · [Sequence and execution examples](docs/EXECUTION_EXAMPLES.md) · [Demo script](docs/DEMO_SCRIPT.md)

## Run without an API key

Python 3.12+ and Node 22.12+. Clone first:

```bash
git clone https://github.com/chen0206a/CampusBridge-Agent.git
cd CampusBridge-Agent
```

From the repository root, on Linux/macOS:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m pip install -e . --no-deps
(cd frontend && npm ci && npm run build)
CAMPUSBRIDGE_MODEL_PROVIDER=mock CAMPUSBRIDGE_ORCHESTRATION=dynamic \
  .venv/bin/python scripts/web_backend.py --port 8015
```

Windows PowerShell equivalents are in the [main README](README.md). Visit http://127.0.0.1:8015/ or http://127.0.0.1:8015/docs. The built client and API share one origin.

Demo users: `student_lan`, `student_he`, `counselor_yu`; public fictional demo password: `CampusDemo-2026!`. MockModel works offline. The application reads process environment variables; `.env.example` is a configuration reference.

## Real-provider experiments

| Stage | Fixed 30 | Natural 20 | Controlled 10 | Boundary 12 |
|---|---:|---:|---:|---:|
| Stage 4 | 26/30 | 17/20 | 9/10 | — |
| Stage 5 | 28/30 | 18/20 | 10/10 | 10/12 |
| Stage 6 Sequential | 28/30 | 18/20 | 10/10 | 10/12 |
| Stage 6 Dynamic | 28/30 | 18/20 | 10/10 | 12/12 |

The Stage 6 paired runs use the same role Prompts, original cases/gold, model alias, generation and checks: thinking disabled, temperature 0, output limit 1024.

| Metric | Sequential | Dynamic |
|---|---:|---:|
| Fixed-set success | 28/30 | 28/30 |
| Boundary success | 10/12 | 12/12 |
| Agent task executions, fixed set | 176 | 150 |
| P50 case latency, fixed set | 4.32s | 3.43s |
| Model requests, fixed set | 125 | 126 |
| Boundary fallback cases | 2 | 0 |

Two unchanged historical references expect MODERATE severity; current outputs are LOW plus an explicit human-support need and create the correct case. They remain risk-label failures in the published scores. Dynamic resolves unrelated evidence and forged user source markers; a missing citation required one additional model request. Latency is a single paired measurement of the whole orchestration policy.

[Full results and reproduction](docs/EXPERIMENTS.md) · [Unchanged archived result bytes and hashes](experiments/README.md)

## Tests and evaluation

In the installed Python environment:

```bash
python -m pytest -q
python scripts/showcase_demo.py --output runs/demo-001
python scripts/http_smoke.py --output runs/http-001.json
python scripts/verify_archive.py
(cd frontend && npm test && npm run build)
```

Clean Windows installation passed **203 backend tests and 27 frontend tests**, the frontend build and the HTTP demonstration. CI covers Ubuntu/Windows on Python 3.12/3.13 and Node 22 frontend tests/builds. It uses MockModel and no external API key. [Clean reproduction report](docs/CLEAN_REPRODUCTION.md)

Optional real providers use server-side `CAMPUSBRIDGE_ENDPOINT`, `CAMPUSBRIDGE_MODEL`, `CAMPUSBRIDGE_API_KEY` and `CAMPUSBRIDGE_MODEL_PROVIDER=openai-compatible`. The frontend receives no model credential. New evaluations freeze a new packet for the current checkout and enforce an explicit cost budget.

## Offline retrieval baseline (Stage A)

[RAG evaluation](docs/RAG_STAGE_A.md) adds 72 fixed synthetic development queries and an isolated 16-document corpus. Current BM25 chunk Recall@1/3/5 is 60.71%/76.79%/76.79%; MRR is 0.7143. Raw results, source bindings and configuration hashes are retained with zero model calls. Production retrieval is unchanged; 228 backend tests pass in this local branch.

## License and scope

[MIT](LICENSE) for original source, Prompts, synthetic knowledge/data and assets. [Provenance](docs/PROVENANCE.md) · [Dependency notices](THIRD_PARTY_NOTICES.md) · [Security](SECURITY.md) · [Contributing](CONTRIBUTING.md).

The system supports student conversations and human follow-up. Professional staff own diagnosis and real intervention. All public demonstration data is fictional.

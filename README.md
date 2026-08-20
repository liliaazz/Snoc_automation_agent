# SNOC AI Agent — Intelligent Telecom Email Automation

> **A production-style, safety-first AI agent that turns telecom support emails into verified, auditable, and controlled business operations.**

[![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python\&logoColor=white)](https://www.python.org/)
[![LangGraph](https://img.shields.io/badge/LangGraph-Workflow-1C3C3C)](https://langchain-ai.github.io/langgraph/)
[![FastAPI](https://img.shields.io/badge/FastAPI-API-009688?logo=fastapi\&logoColor=white)](https://fastapi.tiangolo.com/)
[![React](https://img.shields.io/badge/React-Dashboard-61DAFB?logo=react\&logoColor=black)](https://react.dev/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-Database-4169E1?logo=postgresql\&logoColor=white)](https://www.postgresql.org/)
[![Docker](https://img.shields.io/badge/Docker-Containerized-2496ED?logo=docker\&logoColor=white)](https://www.docker.com/)
[![Tests](https://img.shields.io/badge/Tests-Pytest-0A9EDC?logo=pytest\&logoColor=white)](https://pytest.org/)

---

## 🚀 What is SNOC AI Agent?

SNOC AI Agent is an intelligent email automation platform designed for **telecom support workflows**.

Instead of simply asking an LLM to read an email and execute an action, the system uses a controlled pipeline:

**Email → Conversation Correlation → AI Analysis → Independent Verification → Safety Policy → Execution**

The AI can **propose** an operation, but it never directly controls authorization, business state, API endpoints, or side effects.

This makes the project an exploration of how to build **reliable AI agents for high-impact business workflows**, where correctness, traceability, and safety matter as much as model intelligence.

---

## ✨ Why this project is interesting

This isn't just an LLM wrapper.

The system addresses several real-world problems that appear when deploying AI agents:

* 🧠 **Multi-agent AI workflow** using LangGraph
* 📧 **Real email ingestion** through IMAP/SMTP
* 🧵 **RFC-compliant conversation reconstruction**
* 🔎 **Request and operation-level correlation**
* 🤖 **Independent analyzer + verifier architecture**
* 🛡️ **Deterministic safety and authorization policies**
* 🔁 **Idempotent business operations**
* 🧾 **Full audit trail of model decisions**
* 🚨 **Automatic clarification and escalation**
* 🧪 **Offline replayable email scenarios**
* 📊 **Evaluation and model-comparison framework**
* 🐳 **Dockerized deployment**
* 🗄️ **PostgreSQL production target**
* 💻 **React + dashboard for operational inspection**
* ⚡ **Local and hosted LLM support through OpenAI-compatible APIs and vLLM**

---

# 🏗️ Architecture

```mermaid
flowchart LR
    A[📧 Incoming Email] --> B[IMAP Worker]

    B --> C[Raw Email Storage]
    C --> D[MIME Parser]

    D --> E[Conversation Correlation]
    E --> F[Context Builder]

    F --> G[AI Analyzer]
    G --> H[Independent Verifier]

    H --> I{Safety Policy}

    I -->|Missing Information| J[Clarification]
    I -->|Unsafe / Ambiguous| K[Escalation]
    I -->|Verified & Safe| L[Business API]

    L --> M[Idempotent Execution]
    M --> N[(PostgreSQL)]

    N --> O[Audit Trail]
    O --> P[📊 React Dashboard]
```

The core workflow separates **probabilistic AI reasoning** from **deterministic business controls**.

Models propose and verify operations.

The application decides whether those operations are actually allowed to execute.

---

# 🧠 LangGraph Workflow

The project supports a feature-flagged LangGraph workflow:

```text
Ingress
   ↓
Security
   ↓
NLU
   ↓
Policy
   ↓
Fulfilment
```

The LangGraph implementation keeps authorization, policy enforcement, and side-effect execution deterministic while using LangChain Runnable boundaries around the audited AI components.

A legacy imperative workflow remains available as a rollback path.

```dotenv
WORKFLOW_ENGINE=langgraph
```

The workflow is intentionally designed so that **LLM output cannot directly trigger a business mutation**.

---

# 🛡️ Safety-First AI Architecture

One of the main goals of this project is to explore how LLMs can be used safely in operational automation.

### The model does NOT:

* authorize users
* select business API endpoints
* mutate application state
* directly execute telecom operations
* decide whether an operation is allowed

### The application does:

* authorization
* schema validation
* policy enforcement
* state transitions
* idempotency
* business API execution
* escalation
* audit logging

This separation creates a clear boundary between **AI reasoning** and **business-critical side effects**.

---

# 🔐 Safety Mechanisms

The system includes multiple defensive layers:

### Sender authorization

Normal inbound processing requires an explicit sender whitelist.

```text
Unauthorized sender
        ↓
      Reject
```

### Independent verification

The analyzer proposes an operation.

A separate verifier evaluates that proposal before execution.

```text
Email
 ↓
Analyzer
 ↓
Proposal
 ↓
Verifier
 ↓
Policy
 ↓
Execution
```

### Hard execution invariants

Operations are prevented from executing when there are:

* missing required fields
* contradictory information
* weak conversation correlation
* unsupported evidence
* quoted-history-only evidence
* analyzer/verifier disagreement
* closed or cancelled operations
* ambiguous multi-operation attribution

### Idempotency

Every operation revision receives a stable API idempotency key, preventing duplicate business mutations.

### Quarantine

Malformed or unsafe messages can be quarantined rather than repeatedly reprocessed.

### Auditability

The system records model and workflow information including:

* model/backend
* prompt version
* bounded context hash
* raw and parsed model output
* latency
* token usage
* policy decision
* execution state

---

# 📧 Email Intelligence

The system treats email as a stateful workflow rather than a single prompt.

It supports:

* MIME parsing
* RFC message threading
* message correlation
* Gmail thread IDs
* quoted-history handling
* reply segmentation
* multi-operation requests
* clarification replies
* corrections
* idempotent replay
* escalation

For example:

```text
User:
"Please unblock my account."

        ↓

Agent detects:
Missing OTP

        ↓

Agent:
"Please provide your OTP."

        ↓

User:
"123456"

        ↓

Agent verifies:
Request + reply + conversation

        ↓

Safety policy

        ↓

Business operation
```

The project includes replayable scenarios covering clarification, multi-operation requests, reused chains, uncorrelated replies, idempotency, corrections, and correlation markers.

---

# 🤖 LLM Support

The application is model-provider independent.

It supports:

* deterministic demo backend
* OpenAI-compatible endpoints
* local models
* vLLM deployments
* independently configured analyzer/verifier models

Example:

```dotenv
LLM_PROVIDER=openai_compatible

LLM_BASE_URL=http://127.0.0.1:8000/v1

ANALYZER_MODEL=Qwen2.5-7B-Instruct
VERIFIER_MODEL=Qwen3-8B
```

Hosted vLLM deployments can also be configured independently for different model roles.

The project deliberately does **not bundle model weights** or automatically download large models.

---

# 🖥️ Dashboard

The project includes a React-based operational dashboard.

It provides visibility into:

* processed emails
* conversation chains
* analyzer inputs/outputs
* verifier inputs/outputs
* policy decisions
* request state
* operation state
* execution records
* audit information
* outbound messages
* journey reports

The dashboard is designed primarily for **inspection and debugging**, rather than hiding the agent's decisions behind a black box.

---

# 🧪 Testing & Evaluation

A major part of the project is the evaluation infrastructure.

The default test suite runs without:

* internet access
* credentials
* GPU
* external LLMs
* real email accounts

```bash
pytest
ruff check .
ruff format --check .
mypy src/snoc_agent
```

The repository contains:

* unit tests
* integration tests
* email fixtures
* stateful workflow scenarios
* model-output fixtures
* safety regression tests
* replay tests
* concurrency tests
* mail parser tests
* LangGraph workflow tests
* vLLM smoke tests

---

# 🧪 Offline Replay

You can test the workflow locally without connecting to real email infrastructure.

```bash
python -m snoc_agent.cli.main replay-email \
  tests/fixtures/emails/scenario_a_complete_unblock/01_complete_unblock.eml
```

Or replay a complete scenario:

```bash
python -m snoc_agent.cli.main replay-directory \
  tests/fixtures/emails/scenario_c_multi_operation/
```

Replay mode uses deterministic/demo infrastructure and is forced into dry-run behavior.

This makes the project reproducible for development and debugging.

---

# 🐳 Quick Start with Docker

## 1. Clone the repository

```bash
git clone https://github.com/liliaazz/Snoc_automation_agent.git
cd Snoc_automation_agent
```

## 2. Configure the environment

```bash
cp .env.example .env
```

Edit `.env` and provide the required configuration.

> ⚠️ Never commit `.env`, API keys, passwords, raw email data, or production credentials.

## 3. Start the stack

```bash
docker compose up --build -d postgres migrate api worker
```

The default production-oriented stack uses:

```text
PostgreSQL
   +
API
   +
Worker
   +
LangGraph workflow
```

---

# 💻 Local Development

Python **3.12** is required.

```bash
python3 -m venv .venv
source .venv/bin/activate

pip install -e ".[dev]"

cp .env.example .env

alembic upgrade head
```

For the exact LangChain/LangGraph versions used by the parity suite:

```bash
pip install -c constraints-langchain.txt -e ".[dev]"
```

---

# 🗄️ PostgreSQL

For local PostgreSQL development:

```bash
docker compose up -d postgres

export DATABASE_URL=postgresql+psycopg://snoc_agent:local-development-only@localhost:5432/snoc_agent

alembic upgrade head
```

SQLite remains available as the default development/test database.

PostgreSQL is the deployment target.

---

# 📬 Worker Commands

The main worker components can also be executed independently:

```bash
python -m snoc_agent.cli.main db init

python -m snoc_agent.cli.main mail poll --once

python -m snoc_agent.cli.main outbox send --once

python -m snoc_agent.cli.main processing retry-failed

python -m snoc_agent.cli.main worker run
```

---

# 🔬 Model Evaluation

The repository includes an evaluation framework for comparing analyzer/verifier configurations.

Example:

```bash
python -m snoc_agent.cli.main evaluate \
  --dataset "labeled_data/labeled data/SMOLDATA_last_1000_reviewed.csv" \
  --analyzer-model Qwen2.5-7B-Instruct \
  --verifier-model Qwen3-8B \
  --output-dir outputs/evaluation/qwen25_qwen3
```

The evaluation pipeline supports:

* model comparison
* caching
* resumable runs
* budget limits
* checkpoints
* confusion analysis
* categorized errors
* safety regression subsets
* confidence calibration
* held-out evaluation

---

# 🧰 Tech Stack

| Layer           | Technology                            |
| --------------- | ------------------------------------- |
| Language        | Python 3.12                           |
| AI Workflow     | LangGraph                             |
| LLM Integration | LangChain / OpenAI-compatible APIs    |
| Models          | Qwen / Gemma / configurable providers |
| Model Serving   | vLLM                                  |
| API             | FastAPI                               |
| Database        | PostgreSQL / SQLite                   |
| ORM             | SQLAlchemy                            |
| Migrations      | Alembic                               |
| Frontend        | React                                 |
| Styling         | Tailwind CSS                          |
| Dashboard       | React / operational dashboard         |
| Email           | IMAP / SMTP                           |
| Containers      | Docker / Docker Compose               |
| Testing         | Pytest                                |
| Code Quality    | Ruff / Mypy                           |

---

# 📁 Project Structure

```text
.
├── src/
│   └── snoc_agent/
│       ├── ai/              # AI analysis, verification and model providers
│       ├── api/             # FastAPI application and routes
│       ├── business_api/    # Business API abstraction
│       ├── db/              # Database models and repositories
│       ├── domain/          # Domain entities and state machine
│       ├── evaluation/      # Model evaluation framework
│       ├── graph/           # LangGraph workflow
│       ├── mail/            # IMAP/SMTP and MIME processing
│       ├── prompts/         # Versioned prompts
│       └── workflow/        # Application workflow services
│
├── frontend/                # React dashboard
├── tests/                   # Unit/integration tests and fixtures
├── alembic/                 # Database migrations
├── scripts/                 # Acceptance and evaluation utilities
├── docs/                    # Architecture and migration documentation
├── Dockerfile
├── compose.yaml
├── pyproject.toml
└── .env.example
```

---

# 🎯 Engineering Highlights

This project demonstrates practical experience with:

### AI Engineering

* LLM orchestration
* structured outputs
* prompt versioning
* model-provider abstraction
* model evaluation
* confidence calibration
* local model serving
* LangChain/LangGraph

### Backend Engineering

* FastAPI
* SQLAlchemy
* PostgreSQL
* database migrations
* repository patterns
* state machines
* background workers
* idempotent APIs

### Distributed / Production Concepts

* Docker
* worker coordination
* PostgreSQL advisory locks
* retry mechanisms
* outbox patterns
* audit trails
* failure handling
* concurrency testing

### AI Safety

* deterministic authorization
* independent verification
* safety policies
* input validation
* quarantine
* human escalation
* bounded model context
* controlled side effects

### Frontend

* React
* dashboard architecture
* API integration
* operational monitoring
* audit visualization

---

# ⚠️ Current Limitations

This is a production-style engineering project, but several components remain intentionally limited:

* LDAP/Active Directory is currently an adapter seam.
* Attachment binaries are hashed/described but not analyzed.
* The automated default suite mocks IMAP/SMTP.
* The business API remains mocked in the Docker mailbox journey.
* Qwen confidence values are not calibrated probabilities by default.
* PostgreSQL is the deployment target while automated tests primarily use SQLite.
* Automatic clarification defaults to one round before escalation.
* Corrections to completed operations require human review.
* Some historical datasets lack stateful/RFC ground truth.

See the documentation for details.

---

# 🔐 Security Notice

This repository is designed with safety-oriented defaults, but it should **not be connected to production telecom systems without an environment-specific security review**.

Before enabling live execution, validate:

* authorization sources
* business API permissions
* TLS configuration
* secret management
* backups
* retention policies
* monitoring
* alerting
* production safety thresholds

Never commit:

```text
.env
API keys
passwords
SMTP credentials
IMAP credentials
raw production emails
private datasets
production logs
```

---

# 📚 Documentation

For deeper technical information:

* [Team Handoff](TEAM_HANDOFF.md)
* [LangGraph Migration Status](docs/langgraph_migration_status.md)
* [LangChain/LangGraph Migration Plan](docs/langchain_five_agent_migration_plan.md)
* [vLLM Inference Guide](docs/vllm_inference.md)
* [Weakness & Safety Policy](docs/weakness_policy_decisions.md)
* [Run & Test Guide](RUN_AND_TEST.md)

---


# ⭐ Why this repository exists

SNOC AI Agent was built to explore a practical question:

> **How can we make AI agents useful for real business operations without allowing the model itself to become the authority?**

The project focuses on the engineering around the model:

**reasoning → verification → policy → execution → auditability**

rather than treating an LLM as a black-box automation engine.

If you're interested in **AI agents, LangGraph, LLM safety, telecom automation, or production-oriented AI systems**, feel free to explore the repository.

# RepoAgent

RepoAgent is an autonomous, guarded software-engineering workflow that converts a
natural-language requirement into a tested, reviewed GitHub pull request:

**Understand → Plan → Code → Test → Correct → Review → Create PR**

The final merge always remains a human decision.

For a complete developer handover covering the architecture, every workflow stage,
agents, MCP/A2A communication, setup, security, evaluation, deployment, limitations,
and the teacher's rubric mapping, read **[knowledge.md](knowledge.md)**.

## Architecture

```text
FastAPI / Streamlit
        │
        ▼
CrewAI Flow control plane ───────────────┐
  ├─ Planning Agent                     │ RepoRunState (Pydantic + SQLite)
  ├─ Coding Agent ◄── bounded failures  │
  ├─ Review Agent                       │ callbacks + Langfuse trace
  └─ PR Agent                           │
        │                               │
        ├─ Repository MCP ──────────────┤
        ├─ GitHub MCP ──────────────────┤
        │                               │
        └─ A2A 1.0 JSON-RPC             │
             ▼                          │
        Google ADK Validation Agent     │
             └─ Sandbox MCP ────────────┘
```

The workflow controller—not an LLM—owns state transitions, retry limits, guardrails,
and the PR gate. The Coding Agent receives validation and review failures directly;
there is no separate debugging agent.

## Rubric coverage

| Requirement | Implementation |
|---|---|
| Shared context/state | `RepoRunState` is updated and persisted after every stage. |
| At least two MCP tools | Repository, Sandbox, and GitHub MCP servers are included and called through stdio MCP sessions. |
| Pydantic output | Plans, changes, validation, reviews, PR results, events, state, and final reports are Pydantic models. |
| Agentic patterns | Orchestrator-worker, sequential pipeline, tool use, evaluator-optimizer loop, reflection, bounded retry, and human merge gate. |
| Callbacks/logging | Durable callback events record agent starts/completions/errors, tool stages, transitions, and failures. |
| A2A | CrewAI sends a structured A2A task to an independently served Google ADK Validation Agent. |
| CrewAI + ADK only | CrewAI provides engineering agents/Flow; Google ADK provides validation. No other agent framework is used. |
| Guardrails | Prompt-injection checks, path containment, sensitive-file blocks, command allowlist, secret redaction, resource limits, isolated branch. |
| Observability | Optional Langfuse/OpenTelemetry spans nest the full run, agents, A2A call, and MCP tools. |
| Evaluation | JSONL benchmark runner reports success, validation, retries, latency, tokens, and estimated model cost. |
| Cloud deployment | Docker packaging and a hardened Google Compute Engine deployment guide are included. |

## Project layout

```text
src/repoagent/
  agents/                 CrewAI agents and model-provider adapter
  adk_validation/         Google ADK agent and A2A service
  mcp_servers/            Repository, Sandbox, and GitHub MCP servers
  services/               Deterministic implementations behind MCP
  api.py                   FastAPI control plane
  dashboard.py             Streamlit monitoring UI
  workflow.py              Bounded state machine and correction loop
  models.py                Pydantic contracts and shared state
  events.py                Callbacks, audit events, and redaction
  observability.py         Langfuse tracing
  evaluation.py            End-to-end benchmark harness
tests/                     Unit and workflow tests
runner/                    Restricted validation runner images
deploy/gce/                Cloud deployment guidance
```

## Quick start

Python 3.11–3.13, Git, and optionally Docker are required.

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
python -m pip install -e ".[dev]"
cp .env.example .env
```

Configure at least one model:

```dotenv
REPOAGENT_AGENT_BACKEND=crewai
REPOAGENT_MODEL=openai/gpt-4.1-mini
REPOAGENT_MODEL_API_KEY=...

# Used by Google ADK; GOOGLE_API_KEY or Vertex credentials are also accepted by ADK.
REPOAGENT_ADK_MODEL=gemini-2.5-flash
GOOGLE_API_KEY=...
```

Qwen, DeepSeek, local vLLM/Ollama, and other compatible endpoints can be selected by
changing `REPOAGENT_MODEL` and `REPOAGENT_MODEL_BASE_URL`. This is an adapter boundary;
model support still depends on the selected provider's API and structured-output quality.

For local development without Docker:

```dotenv
REPOAGENT_SANDBOX_MODE=local
```

Local mode executes only allow-listed programs with `shell=False`, but it is not suitable
for untrusted public repositories. Use the Docker runner on a dedicated machine for those.

## Run the services

Open three terminals in the activated environment:

```bash
repoagent-adk
repoagent-api
streamlit run src/repoagent/dashboard.py
```

Open `http://localhost:8501`. API documentation is available at
`http://localhost:8000/docs`; the ADK Agent Card is at
`http://localhost:8100/.well-known/agent-card.json`.

CLI example:

```bash
repoagent run \
  --repo "C:/projects/example" \
  --requirement "Add password-reset functionality with expiring tokens" \
  --base main \
  --draft-pr
```

Use `--no-create-pr` while developing or evaluating.

## Docker runners

Build the runner images before using `REPOAGENT_SANDBOX_MODE=docker`:

```bash
docker build -f runner/python.Dockerfile -t repoagent-runner-python:latest .
docker build -f runner/node.Dockerfile -t repoagent-runner-node:latest .
```

The runner uses an ephemeral repository copy, no network, dropped Linux capabilities,
`no-new-privileges`, an unprivileged user, and CPU/memory/process/time limits. Create
project-specific runner images when the target repository needs additional dependencies.

`docker compose up --build` starts the classroom/demo deployment. The compose validation
service uses local execution inside its own container; the hardened cloud setup should use
the dedicated Docker runner described in `deploy/gce/README.md`.

## GitHub setup

Set `REPOAGENT_GITHUB_TOKEN` to a fine-grained token or GitHub App installation token with
only the target repository's Contents and Pull Requests permissions. The local Git remote
must also be configured to push the generated `repoagent/*` branch. RepoAgent refuses to
push `main` or `master` and never merges a pull request.

For a production installation, replace a long-lived token with a short-lived GitHub App
installation token and put authentication in the Git credential helper rather than a URL.

## State, callbacks, and traces

State and audit events are stored at `.repoagent/repoagent.db`. The API exposes:

- `POST /runs`
- `GET /runs`
- `GET /runs/{run_id}`
- `GET /runs/{run_id}/events`
- `GET /runs/{run_id}/report`

Set the Langfuse keys and `REPOAGENT_LANGFUSE_ENABLED=true` to export nested traces. Source
and tool data is redacted for common token formats before it reaches the durable event log;
configure an additional Langfuse masking function for institution-specific secrets.

## Evaluation

Copy `evaluation/cases.example.jsonl`, point every case at a disposable benchmark
repository, and run:

```bash
python -m repoagent.evaluation evaluation/cases.jsonl \
  --output evaluation/results.json
```

Run the same cases for these configurations:

1. One-shot model baseline.
2. RepoAgent with `REPOAGENT_MAX_CORRECTIONS=0`.
3. Full RepoAgent with bounded correction and review.

Use at least ten tasks, three runs per task, hidden acceptance tests, and a mixture of
features, bugs, refactors, unsafe requests, and impossible requests. Pricing is not
hard-coded because provider prices change; set:

```dotenv
REPOAGENT_MODEL_INPUT_COST_PER_MILLION=...
REPOAGENT_MODEL_OUTPUT_COST_PER_MILLION=...
```

The result contains task completion rate, validation pass rate, mean corrections, mean and
p95 duration, input/output tokens, and estimated hosted-model cost.

## Safety boundaries

- Repository contents are untrusted data, not agent instructions.
- `.env`, `.git`, and CI workflow changes are blocked by default.
- Generated paths must remain inside the isolated clone.
- Validation commands are argument arrays, never shell strings.
- Test execution stops on the first failure and is bounded by timeout/output limits.
- Correction attempts are finite; unresolved failures become `needs_human`.
- PR creation occurs only after validation and review pass.
- The system creates a branch and PR but never performs the final merge.

See [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md) and
[docs/EVALUATION.md](docs/EVALUATION.md) for the assessment-ready details.


# RepoAgent: Complete Project Knowledge Transfer

This document is the technical handover for RepoAgent. It explains what the project does, why it was designed this way, how every major component communicates, how to run it, how it satisfies the evaluation criteria, and what is still required before a real production deployment.

> Important: RepoAgent can prepare a branch and create a pull request, but it never merges the pull request. A human remains the final decision-maker.

## 1. Project in one paragraph

RepoAgent is an agentic AI software-engineering system that accepts a natural-language change request and an existing Git repository, creates an isolated working branch, studies the repository, plans the change, edits files, runs validation, sends failures back to the Coding Agent for bounded correction, reviews the final diff, and optionally creates a GitHub pull request. CrewAI implements the planning, coding, review, and pull-request agents. A separate Google ADK agent performs validation. The CrewAI side communicates with the ADK side using the A2A protocol, while repository, sandbox, and GitHub operations are exposed through MCP tools. Shared state is modeled with Pydantic and persisted in SQLite.

The intended workflow is:

```text
Understand -> Plan -> Code -> Test -> Correct -> Review -> Create PR
```

## 2. Why this is an agentic AI project

This is more than a chatbot or a one-shot code generator because it has:

- multiple specialized agents with separate roles;
- a deterministic controller that decides what stage executes next;
- durable shared state passed between stages;
- real tool use through MCP;
- agent-to-agent interoperability through A2A;
- an evaluator-optimizer loop in which test/review feedback returns to the Coding Agent;
- bounded autonomy, safety checks, audit events, and a human merge gate;
- structured Pydantic inputs and outputs at system boundaries.

The language model proposes plans, code, reviews, and PR text. It does **not** control retry limits, status transitions, path policy, command policy, or the permission to create a PR. Those decisions stay in deterministic Python code.

## 3. High-level architecture

```text
User
  |  requirement + repository + options
  v
FastAPI / CLI / Streamlit Dashboard
  |
  v
RepoAgentRuntime
  |
  v
CrewAI Flow + deterministic WorkflowEngine
  |-- Prompt Guard
  |-- Planning Agent (CrewAI)
  |-- Coding Agent (CrewAI) <-----------------------+
  |-- Review Agent (CrewAI)                         |
  |-- PR Agent (CrewAI)                             |
  |                                                 |
  |-- Repository MCP -> isolated clone/file/Git     |
  |-- GitHub MCP -> GitHub Pull Requests API        |
  |                                                 |
  +-- A2A JSON-RPC 1.0 -> Google ADK Validation Agent
                              |
                              +-- Sandbox MCP -> tests/lint/build
                                      |
                                      +-- failure feedback --------+

Shared across the run:
  Pydantic RepoRunState <-> SQLite state/events
  callback event log
  optional Langfuse trace
```

## 4. Main technologies and their purpose

| Technology | Purpose in RepoAgent |
|---|---|
| Python 3.11-3.13 | Main implementation language |
| CrewAI | Planning, coding, review, PR-writing agents and top-level Flow wrapper |
| Google ADK | Independent validation agent |
| A2A SDK/protocol | CrewAI-to-ADK agent communication |
| MCP | Standard tool interface for repository, sandbox, and GitHub actions |
| Pydantic | Validated requests, state, agent outputs, reports, events, and configuration |
| FastAPI | REST API and the ADK/A2A service |
| Streamlit | Operator dashboard |
| SQLite | Durable run state and callback event storage |
| Langfuse | Optional tracing/observability |
| Docker | Restricted validation environment and service packaging |
| Git/GitHub API | Isolated branches, commits, pushes, and pull requests |
| Pytest/Ruff | Project tests and static checks |

Pinned framework versions are recorded in `pyproject.toml` and the exact resolved dependency graph is recorded in `uv.lock`.

## 5. Repository map

```text
repoagent/
|-- README.md                         Quick-start and project overview
|-- knowledge.md                      This complete handover document
|-- pyproject.toml                    Package metadata, dependencies, CLI commands
|-- uv.lock                           Reproducible dependency lockfile
|-- .env.example                      Safe configuration template (no real secrets)
|-- Dockerfile                        Main service image
|-- docker-compose.yml                API + validation + dashboard demo stack
|-- runner/
|   |-- python.Dockerfile             Restricted Python validation image
|   `-- node.Dockerfile               Restricted Node validation image
|-- deploy/gce/README.md              Google Compute Engine deployment guide
|-- docs/
|   |-- THREAT_MODEL.md               Security boundaries, threats, mitigations
|   `-- EVALUATION.md                 End-to-end evaluation protocol
|-- evaluation/
|   `-- cases.example.jsonl           Example benchmark dataset
|-- src/repoagent/
|   |-- runtime.py                    Dependency wiring and backend selection
|   |-- workflow.py                   Workflow state machine and correction loop
|   |-- models.py                     Pydantic contracts and shared state
|   |-- config.py                     Environment-based settings
|   |-- guardrails.py                 Prompt and path safeguards
|   |-- events.py                     Callbacks, redaction, audit events
|   |-- store.py                      SQLite state/event persistence
|   |-- observability.py              Optional Langfuse spans
|   |-- mcp_client.py                 MCP process client and tool gateway
|   |-- a2a_client.py                 A2A validation client
|   |-- api.py                        FastAPI control-plane API
|   |-- dashboard.py                  Streamlit UI
|   |-- cli.py                        Command-line interface
|   |-- evaluation.py                 Benchmark runner and metric aggregation
|   |-- agents/
|   |   |-- base.py                   Provider-neutral agent contract
|   |   |-- provider.py               CrewAI model adapter
|   |   |-- crewai_backend.py         CrewAI specialized agents
|   |   `-- deterministic.py          Offline structural smoke-test backend
|   |-- adk_validation/
|   |   |-- agent.py                  Google ADK validation agent
|   |   `-- service.py                A2A JSON-RPC service and Agent Card
|   |-- mcp_servers/
|   |   |-- repository_server.py      Repository MCP server
|   |   |-- sandbox_server.py         Sandbox MCP server
|   |   `-- github_server.py          GitHub MCP server
|   `-- services/
|       |-- repository.py             Repository/file/Git implementation
|       |-- sandbox.py                Command policy and execution
|       `-- github.py                 GitHub Pull Requests API implementation
`-- tests/                             Unit, protocol, state, safety, and workflow tests
```

## 6. Agents and responsibilities

### 6.1 Planning Agent (CrewAI)

Input:

- normalized requirement;
- repository summary;
- selected repository file contents.

Output: `ImplementationPlan`, containing a summary, assumptions, ordered file-specific steps, acceptance criteria, and risks.

The agent only plans. It does not claim that files were already changed.

### 6.2 Coding Agent (CrewAI)

Input:

- normalized requirement;
- approved implementation plan;
- current repository context;
- validation or review feedback from the preceding attempt, when present.

Output: `ChangeSet`, containing complete create/update/delete operations. For a created or updated file, the model must return the complete final content, not a patch fragment or placeholder.

There is intentionally no Debugging Agent. Failed validation output and requested review changes are routed directly back to this same Coding Agent.

### 6.3 Validation Agent (Google ADK)

The validation agent is independently hosted on port `8100`. It receives a `ValidationRequest` from the CrewAI control plane over A2A. Its only approved action is to call the Sandbox MCP tool exactly once using the supplied repository path, command list, and attempt number.

Output: `ValidationReport`, including every executed command, exit code, stdout, stderr, duration, timeout status, summary, and recommended actions.

The ADK agent is not trusted to invent validation results. The validated result returned by the Sandbox MCP tool is captured and used as the official report.

### 6.4 Code Review Agent (CrewAI)

Input:

- original requirement;
- plan;
- most recent validation report;
- current Git diff.

Output: `ReviewReport`, with approval status and evidence-based findings categorized as `info`, `warning`, or `critical`.

The workflow proceeds only if the review is approved and has no critical findings. Otherwise, review feedback returns to the Coding Agent, subject to the same correction limit.

### 6.5 Pull Request Agent (CrewAI)

Input:

- requirement;
- plan;
- successful validation evidence;
- approved review;
- list of changed files.

Output: `PullRequestDraft` with a concise title and a Markdown body containing Summary, Changes, Validation, Review, and Human Checklist sections.

The Repository MCP commits and pushes the branch; the GitHub MCP then creates the PR. The system refuses to push `main` or `master` and has no merge function.

## 7. Exact end-to-end workflow

### Step 1: Accept a run

A run can be started from the CLI, FastAPI, or Streamlit. The request is validated as a `RunRequest` with:

- `requirement`;
- `repository` (local Git path or HTTPS Git URL only);
- `base_branch`;
- whether a PR should be created;
- whether the PR should be a draft;
- optional explicit validation commands.

The API returns a run ID immediately and performs the workflow in a background task.

### Step 2: Guard the requirement

The status becomes `guarding`. `PromptGuard` normalizes whitespace and checks for high-confidence prompt-injection and destructive/malicious patterns. Injection or malicious requests end in `blocked`. Abusive language is flagged but is not currently a blocking condition.

### Step 3: Prepare and analyze an isolated workspace

The status becomes `analyzing`. Through the Repository MCP, RepoAgent:

1. accepts an existing local Git repository or clones an HTTPS Git repository;
2. creates a run-specific workspace under `.repoagent/workspaces`;
3. checks out a generated `repoagent/<run-id>` branch;
4. detects languages, manifests, relevant files, and likely validation commands;
5. builds a bounded repository tree/context summary.

The original working tree and default branch are not edited directly.

### Step 4: Plan

The status becomes `planning`. Selected files are read through the Repository MCP. Repository text is treated as untrusted data and high-confidence prompt-injection text is neutralized before being given to the model. The Planning Agent returns a Pydantic-validated `ImplementationPlan`.

### Step 5: Code

The status becomes `coding` on the first attempt or `correcting` after feedback. The Coding Agent returns a Pydantic-validated `ChangeSet`. Before writing anything, `PathGuard` verifies:

- every path is relative;
- no path traversal is present;
- the resolved target remains under the isolated repository root;
- `.git`, `.env`, Git hooks, and `.github/workflows` are not modified;
- a single change set touches no more than 100 files;
- an individual generated file does not exceed 1 MB.

The Repository MCP applies the approved change set.

### Step 6: Validate over A2A

The status becomes `validating`. The controller creates a `ValidationRequest` and sends an A2A 1.0 JSON-RPC `message/send` request to the Google ADK service. The ADK agent calls the Sandbox MCP.

If the user did not provide commands, repository inspection chooses commands such as:

- `python -m pytest -q` for Python tests;
- `npm test -- --runInBand` for Node projects;
- `go test ./...` for Go;
- `cargo test --locked` for Rust;
- `git diff --check` as a minimal fallback.

Validation stops at the first failing command so that feedback is focused and execution is bounded.

### Step 7: Correct failures

When validation fails, captured stdout/stderr and recommended actions are sent directly to the Coding Agent. The controller increments `correction_count` and repeats Code -> Validate.

The same loop is used if validation passes but code review requests changes:

```text
Coding Agent -> Validation Agent -> Review Agent
      ^              | failed          | changes requested
      +--------------+------------------+
```

The default maximum is three corrections. When the limit is reached, the status becomes `needs_human`; the system does not retry forever.

### Step 8: Review

After validation passes, the status becomes `reviewing`. The Repository MCP supplies the Git diff. The Review Agent checks correctness, security, regressions, missing tests, and requirement coverage.

### Step 9: Complete or create a PR

If `create_pull_request=false`, the successful run ends as `completed` without pushing.

If PR creation is enabled:

1. status becomes `pr_creating`;
2. the PR Agent drafts a title and evidence-based Markdown body;
3. the Repository MCP commits and pushes the generated branch;
4. the GitHub MCP calls GitHub's Pull Requests API;
5. a successful result ends as `completed`;
6. a PR failure ends as `needs_human` with the error saved in state.

### Step 10: Human decision

A developer reads the diff, validation evidence, review findings, and checklist on GitHub. Only the human can approve and merge.

## 8. Shared state and context passing

`RepoRunState` is the central shared state object. It is Pydantic-validated on assignment and contains:

- run ID, timestamps, and current status;
- original `RunRequest`;
- isolated workspace path and working branch;
- guardrail result;
- repository summary;
- implementation plan;
- every change set from every attempt;
- every validation report;
- every review report;
- PR result;
- correction count and maximum;
- trace/failure information;
- runtime, token, cost, agent-call, and tool-call metrics.

The state is saved to `.repoagent/repoagent.db` after meaningful transitions. This is how intermediate output from one agent becomes the input to the next:

| Producer | State written | Consumer |
|---|---|---|
| Prompt Guard | `guardrail` | Planning and Coding Agents |
| Repository MCP | `repository_summary` | Planning and Coding Agents |
| Planning Agent | `plan` | Coding, Review, and PR Agents |
| Coding Agent | `change_sets` and repository files | Validation and Review Agents |
| ADK Validation Agent | `validation_reports` | Coding, Review, and PR Agents |
| Review Agent | `review_reports` | Coding Agent or PR Agent |
| GitHub MCP | `pull_request` | Final report and dashboard |

This fulfills the context-sharing requirement with explicit state rather than hidden conversation history.

## 9. Structured Pydantic outputs

Major schemas in `src/repoagent/models.py` include:

| Schema | Meaning |
|---|---|
| `RunRequest` | Validated user input |
| `GuardrailResult` | Safety decision and flags |
| `RepositorySummary` | Repository structure, languages, manifests, tests |
| `ImplementationPlan` / `PlanStep` | Planner output |
| `ChangeSet` / `FileChange` | Coding output |
| `CommandResult` | One validation command result |
| `ValidationRequest` / `ValidationReport` | A2A validation contract |
| `ReviewReport` / `ReviewFinding` | Review output |
| `PullRequestDraft` / `PullRequestResult` | PR contract |
| `RepoRunState` | Complete shared workflow state |
| `RunEvent` | Callback/audit event |
| `RepoAgentFinalReport` | Final structured result |

The CLI renders the final report as Markdown. The API returns JSON. Evaluation results are written as JSON. This satisfies the structured-output requirement in multiple formats.

## 10. MCP tools

RepoAgent includes three external tool servers, each launched as a separate stdio MCP process by `MCPProcessClient`.

### Repository MCP

Responsibilities:

- prepare an isolated workspace and branch;
- inspect repository structure and infer test commands;
- read selected file context;
- apply a structured change set;
- generate a diff and changed-file list;
- commit and push the generated branch.

### Sandbox MCP

Responsibilities:

- validate argument-array commands against an allowlist;
- reject shell operators and dangerous programs;
- run commands locally for development or inside a restricted Docker container;
- enforce timeout and output limits;
- return a structured `ValidationReport`.

### GitHub MCP

Responsibilities:

- call the GitHub Pull Requests API using a narrowly scoped token;
- create a draft or ready PR;
- return a structured PR result without merging it.

This exceeds the rubric's requirement of at least two MCP-integrated tools.

## 11. A2A interoperability

The two agent frameworks are separated by a real service/protocol boundary:

```text
CrewAI WorkflowEngine
  -> HTTP POST /a2a
  -> A2A-Version: 1.0
  -> JSON-RPC method: message/send
  -> ValidationRequest data part
  -> Google ADK Validation Agent
  -> validation-report artifact
  -> ValidationReport parsed by Pydantic
```

The ADK service exposes:

- `GET /.well-known/agent-card.json` - A2A Agent Card;
- `POST /a2a` - JSON-RPC endpoint;
- `POST /message:send` - REST-compatible send endpoint;
- `GET /tasks/{task_id}` - stored task state;
- `GET /healthz` - health and framework/protocol identity.

The CrewAI and ADK agents do not share in-memory Python objects. Their contract is the A2A message and its structured artifact, demonstrating interoperability.

## 12. Agentic design patterns used

| Pattern | Where it appears |
|---|---|
| Orchestrator-worker | `WorkflowEngine` controls specialized agents and tools |
| Sequential pipeline | Guard -> Analyze -> Plan -> Code -> Validate -> Review -> PR |
| Tool use | MCP repository, sandbox, and GitHub calls |
| Evaluator-optimizer | Validation/Review evaluate Coding Agent output and return feedback |
| Reflection/correction | Coding Agent receives concrete prior failure information |
| Bounded retry | `max_corrections` prevents infinite loops |
| Shared blackboard/state | `RepoRunState` persisted in SQLite |
| Human-in-the-loop gate | Human must approve/merge the generated PR |
| Structured contracts | Pydantic at agent, tool, A2A, API, and final-output boundaries |
| Deterministic policy layer | Python controller, not an LLM, owns safety and transitions |

## 13. Monitoring, callbacks, logging, and observability

### Callback events

`AgentCallback` records:

- agent start;
- intermediate step metadata;
- agent completion and token usage;
- agent failure.

`EventBus` also records workflow transitions, applied changes, validation results, reviews, retry routing, guardrail blocks, unhandled errors, and final status. Events are written to SQLite and exposed by the API/dashboard.

Raw hidden reasoning is intentionally not stored. Callback steps record metadata such as step type and tool name, which is enough for flow monitoring without persisting private chain-of-thought.

### Secret redaction

Before durable logging, common API key, token, secret, password, GitHub token, and OpenAI-style key patterns are replaced with `[REDACTED]`.

### Langfuse

When enabled, nested Langfuse/OpenTelemetry observations cover the overall run, CrewAI agent calls, MCP calls, and the A2A validation call. Tracing is fail-open for availability: if Langfuse cannot initialize or flush, it does not break the engineering workflow.

### Dashboard and API

The dashboard shows current status, correction count, agent/tool calls, duration, event timeline, plan, validation reports, shared state, failures, and the PR URL.

API endpoints:

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/healthz` | API health |
| `POST` | `/runs` | Start a background run |
| `GET` | `/runs` | List recent runs |
| `GET` | `/runs/{run_id}` | Read complete shared state |
| `GET` | `/runs/{run_id}/events` | Read the audit timeline |
| `GET` | `/runs/{run_id}/report` | Read the structured final report |

## 14. Security and guardrails

### Input controls

- requirement length and repository/base-branch formats are Pydantic-validated;
- only local paths and HTTPS Git URLs are accepted;
- common instruction-injection patterns are blocked;
- clearly destructive or credential-stealing requests are blocked;
- abusive language is recorded as a flag.

### Repository-content controls

- repository files are treated as untrusted data;
- detected injection text in repository context is neutralized;
- context and diff sizes are bounded before model calls.

### File controls

- paths are normalized and containment-checked;
- traversal and absolute paths are rejected;
- sensitive files and CI workflows are blocked by default;
- change-set file count and generated file size are limited.

### Command controls

- commands are lists of arguments, never shell strings;
- `shell=False` is always used;
- only approved programs are permitted;
- shell operators and dangerous command tokens are rejected;
- commands have time and output limits;
- validation stops on first failure.

### Docker runner controls

Docker validation uses:

- an ephemeral copy excluding `.git`, `.env`, `.repoagent`, and `node_modules`;
- disabled networking;
- one CPU and 1 GB memory;
- a 256-process limit;
- `no-new-privileges`;
- all Linux capabilities dropped;
- an unprivileged user;
- a disposable container.

Local sandbox mode is convenient for development but is **not** sufficient for untrusted public repositories.

### Git/GitHub controls

- work occurs on a generated branch;
- force push is not used;
- pushing `main` or `master` is refused;
- PR creation requires successful validation and review;
- RepoAgent never merges.

See `docs/THREAT_MODEL.md` for remaining production-hardening work such as OIDC, tenant isolation, organization allowlists, rootless Docker/gVisor, immutable audit retention, quotas, and dependency/image scanning.

## 15. Setup for another developer

### Prerequisites

- Python 3.11, 3.12, or 3.13;
- Git;
- Docker Desktop/Engine if using the Docker sandbox;
- one CrewAI-compatible model credential;
- a Google/Gemini credential for ADK;
- a GitHub token only if actual PR creation is required.

### Clone and install (PowerShell)

```powershell
git clone https://github.com/Zaavi14429h/repoagent.git
cd repoagent
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

### Clone and install (Linux/macOS)

```bash
git clone https://github.com/Zaavi14429h/repoagent.git
cd repoagent
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
cp .env.example .env
```

Never commit `.env`; it is intentionally ignored by Git.

## 16. Configuration

Minimum model configuration in `.env`:

```dotenv
REPOAGENT_AGENT_BACKEND=crewai
REPOAGENT_MODEL=openai/gpt-4.1-mini
REPOAGENT_MODEL_API_KEY=replace-with-real-key

REPOAGENT_ADK_MODEL=gemini-2.5-flash
GOOGLE_API_KEY=replace-with-real-key
```

For an OpenAI-compatible Qwen, DeepSeek, local vLLM, or other provider endpoint:

```dotenv
REPOAGENT_MODEL=provider/model-name
REPOAGENT_MODEL_API_KEY=replace-with-provider-key
REPOAGENT_MODEL_BASE_URL=https://provider.example/v1
```

Provider compatibility depends on the provider's CrewAI/LiteLLM interface and its ability to produce reliable structured output.

Useful settings:

| Variable | Meaning | Default |
|---|---|---|
| `REPOAGENT_DATABASE_PATH` | SQLite state/event database | `.repoagent/repoagent.db` |
| `REPOAGENT_WORKSPACE_ROOT` | Isolated clones | `.repoagent/workspaces` |
| `REPOAGENT_MAX_CORRECTIONS` | Validation/review correction limit | `3` |
| `REPOAGENT_AGENT_BACKEND` | `crewai` or offline `deterministic` | `crewai` |
| `REPOAGENT_ADK_A2A_URL` | ADK service base URL | `http://localhost:8100` |
| `REPOAGENT_SANDBOX_MODE` | `docker` or `local` | `docker` |
| `REPOAGENT_GITHUB_TOKEN` | Fine-grained token/App token for PR creation | empty |
| `REPOAGENT_LANGFUSE_ENABLED` | Enable trace export | `false` |
| `LANGFUSE_PUBLIC_KEY` | Langfuse credential | empty |
| `LANGFUSE_SECRET_KEY` | Langfuse credential | empty |
| `LANGFUSE_BASE_URL` | Langfuse server | `https://cloud.langfuse.com` |

If pricing is known for the selected model, set:

```dotenv
REPOAGENT_MODEL_INPUT_COST_PER_MILLION=0
REPOAGENT_MODEL_OUTPUT_COST_PER_MILLION=0
```

Replace `0` with the provider's recorded price snapshot for meaningful cost estimates.

## 17. How to run locally

### Option A: three development terminals

Activate `.venv` in each terminal.

Terminal 1 - ADK/A2A validation service:

```powershell
repoagent-adk
```

Terminal 2 - main API:

```powershell
repoagent-api
```

Terminal 3 - dashboard:

```powershell
streamlit run src/repoagent/dashboard.py
```

Open:

- dashboard: `http://localhost:8501`;
- API documentation: `http://localhost:8000/docs`;
- ADK Agent Card: `http://localhost:8100/.well-known/agent-card.json`.

For trusted local development without Docker, set:

```dotenv
REPOAGENT_SANDBOX_MODE=local
```

### Option B: CLI

The ADK service still needs to be running:

```powershell
repoagent run --repo "C:\projects\target-project" --requirement "Add password reset with expiring tokens" --base main --draft-pr
```

To test the full coding/validation/review flow without pushing or creating a PR:

```powershell
repoagent run --repo "C:\projects\target-project" --requirement "Add password reset with expiring tokens" --base main --no-create-pr --output run-report.json
```

Inspect a previous run:

```powershell
repoagent show RUN_ID
repoagent show RUN_ID --json
repoagent events RUN_ID
```

### Option C: Docker Compose classroom/demo stack

First create `.env`, then run:

```powershell
docker compose up --build
```

The Compose file starts the API, validation service, and dashboard. Its validation container uses local process execution inside that service container for demo convenience. The hardened design uses the dedicated runner images.

### Build restricted runners

```powershell
docker build -f runner/python.Dockerfile -t repoagent-runner-python:latest .
docker build -f runner/node.Dockerfile -t repoagent-runner-node:latest .
```

## 18. GitHub token and repository requirements

Use a fine-grained personal access token or GitHub App installation token limited to the target repository. It needs only the permissions required to push contents and create pull requests.

The target repository must:

- be a valid Git repository;
- have the specified base branch;
- have an `origin` GitHub remote if PR creation is enabled;
- be pushable by the current Git credential setup;
- allow the configured token to create pull requests.

Do not embed tokens in Git remote URLs. Use a credential helper or short-lived GitHub App token.

## 19. Testing and quality checks

Run all tests:

```powershell
python -m pytest -q
```

Run linting:

```powershell
python -m ruff check .
python -m ruff format --check .
```

Check dependency consistency:

```powershell
python -m pip check
```

The included test suite covers:

- prompt, path, and sensitive-file guardrails;
- local sandbox command validation and execution;
- repository inspection and change application;
- SQLite state/event persistence;
- A2A client response extraction;
- ADK/A2A service behavior and Agent Card;
- success, retry, and correction-limit workflow paths.

At the time this handover was written, the local suite passed 17 tests. Re-run the commands above on every new machine and after every change; the repository history, environment, or dependency platform may differ.

## 20. Evaluation plan

Benchmark cases use JSON Lines. Start from `evaluation/cases.example.jsonl`, point each case at a disposable benchmark repository, and run:

```powershell
python -m repoagent.evaluation evaluation/cases.jsonl --output evaluation/results.json
```

The evaluator measures:

- total/completed cases and task-completion rate;
- validation pass rate;
- mean correction iterations;
- mean and p95 duration;
- input/output tokens;
- estimated hosted-model cost;
- the complete structured result for each run.

For a defensible experiment, compare:

1. a one-shot non-agentic model baseline;
2. RepoAgent with `REPOAGENT_MAX_CORRECTIONS=0`;
3. full RepoAgent with bounded correction and review.

Use at least ten tasks and three runs per task. Include features, bugs with hidden regression tests, refactors, prompt-injection repositories, unsafe requests, and impossible requests. Pin commit hashes, models, prompts, runner images, lockfiles, and temperature. Preserve reports, events, traces, test logs, PR URLs, and the date/provider pricing snapshot.

Do not count a run as successful merely because it created a PR. The correct success criterion is that hidden acceptance tests pass, no critical review issue remains, and the diff is correctly scoped.

## 21. Cloud deployment

The included reference deployment targets a dedicated Google Compute Engine VM because the API and validation service need access to run workspaces.

Summary:

1. create a dedicated Ubuntu VM (suggested minimum: 4 vCPU, 8 GB RAM, 40 GB disk);
2. install Docker Engine and Compose;
3. clone the repository into `/opt/repoagent`;
4. store secrets in Google Secret Manager and inject them only at startup;
5. build the restricted Python and Node runner images;
6. use an unprivileged service user and rootless Docker for production;
7. expose only HTTPS/443 through a load balancer;
8. keep ports 8000 and 8100 private;
9. put Identity-Aware Proxy or another OIDC proxy in front of the dashboard/API;
10. apply independent egress firewall rules and monitoring.

`docker compose up -d` is suitable for a classroom demonstration, not a finished multi-tenant production environment. Detailed instructions and cautions are in `deploy/gce/README.md`.

## 22. Statuses and failure behavior

| Status | Meaning |
|---|---|
| `queued` | Run exists but has not started |
| `guarding` | Requirement safety checks are running |
| `analyzing` | Isolated repository workspace is being prepared/inspected |
| `planning` | Planning Agent is producing the implementation plan |
| `coding` | First implementation attempt |
| `validating` | ADK/Sandbox validation is running |
| `correcting` | Coding Agent is repairing feedback |
| `reviewing` | Code Review Agent is checking the validated diff |
| `pr_creating` | Branch push and PR creation are running |
| `completed` | Requested successful workflow finished |
| `needs_human` | Bounded autonomy stopped and requires intervention |
| `blocked` | Guardrail rejected the request |
| `failed` | Unexpected system/service failure |
| `cancelled` | Reserved terminal cancellation state |

Examples that lead to `needs_human` include an empty Coding Agent change set, reaching the correction limit, or a GitHub PR creation failure after successful implementation.

## 23. Current limitations and honest project status

- A real model-backed run requires valid CrewAI/provider and Google ADK credentials.
- Actual PR creation requires a GitHub token plus working Git push authentication.
- Provider adapters depend on compatible hosted/local interfaces and reliable structured output.
- The offline deterministic backend validates wiring but intentionally produces no source changes.
- Local sandbox mode is not secure enough for hostile repositories.
- Docker runner images contain a general baseline; repositories with unusual dependencies need project-specific runner images.
- The reference cloud deployment guide and Docker artifacts are implemented, but deployment is not automatic and needs the owner's cloud account, secrets, DNS, and security setup.
- The API/dashboard still need authentication, authorization, rate limits, tenant isolation, and production-grade secret management before public exposure.
- SQLite is appropriate for local/classroom use; a distributed deployment would need a stronger shared persistence/queue design.
- The ADK service currently stores its A2A task cache in memory, while the authoritative RepoAgent run state is durable in SQLite.
- Model quality, runtime, and cost must be measured using the evaluation suite; they cannot be guaranteed by architecture alone.

## 24. Teacher evaluation criteria mapping

| # | Required criterion | RepoAgent evidence |
|---:|---|---|
| 1 | Context sharing and state updates | `RepoRunState` carries plans, changes, validation, review, PR data, and metrics between stages; `StateStore` persists it after transitions. |
| 2 | At least two tools using MCP | Repository MCP, Sandbox MCP, and GitHub MCP are separate stdio tool servers. |
| 3 | Structured Pydantic output | All inputs, agent outputs, A2A messages, reports, state, events, and final results use Pydantic; CLI produces Markdown and API/evaluator produce JSON. |
| 4 | Suitable agentic design patterns | Orchestrator-worker, sequential pipeline, tool use, evaluator-optimizer, correction/reflection, bounded retry, shared state, and human gate. |
| 5 | Monitoring/logging with callbacks | `AgentCallback` and `EventBus` track starts, progress metadata, completion, errors, transitions, validations, and retries in SQLite and the dashboard. |
| 6 | Agent-to-agent communication with A2A | CrewAI control plane sends `ValidationRequest` to the independent Google ADK service over A2A JSON-RPC 1.0 and receives a structured artifact. |
| 7 | Both CrewAI and ADK, no other agent framework | CrewAI implements software-engineering agents/Flow; Google ADK implements validation; FastAPI/MCP/A2A are infrastructure/protocols, not extra agent frameworks. |
| 8 | Security and guardrails | Prompt/injection checks, Pydantic validation, untrusted-content handling, path containment, sensitive-file policy, command allowlist, Docker restrictions, secret redaction, retry limits, and no auto-merge. |
| 9 | Observability and provenance | Durable state/events plus optional nested Langfuse traces for run, agent, A2A, and tool operations. |
| 10 | End-to-end performance, cost, runtime evaluation | JSONL benchmark harness aggregates completion/pass rates, corrections, mean/p95 duration, tokens, and configured cost estimates; full experimental method is documented. |
| 11 | Cloud deployment | Docker packaging, Compose demo stack, restricted runners, and a GCE deployment/hardening guide are included. Actual deployment requires cloud credentials. |

## 25. Suggested demonstration sequence

For a teacher or reviewer:

1. show this document and the architecture in `README.md`;
2. open `models.py` to show Pydantic shared state/contracts;
3. open `workflow.py` to show deterministic orchestration and correction routing;
4. open `crewai_backend.py` to show the four CrewAI roles;
5. open `adk_validation/agent.py` and `service.py` to show Google ADK and A2A;
6. open the three `mcp_servers` to show tool interoperability;
7. open `guardrails.py`, `services/sandbox.py`, and `THREAT_MODEL.md` for safety;
8. run `python -m pytest -q`;
9. start ADK, API, and Streamlit and show the live event timeline;
10. run a disposable repository task with `--no-create-pr` first;
11. show the generated final Markdown/JSON report and SQLite-backed events;
12. run the benchmark harness and show timing/cost metrics;
13. only then demonstrate PR creation on a repository that can safely receive a generated branch.

## 26. Rules for future contributors

- Keep workflow policy deterministic; do not give retry/safety/merge control to an LLM.
- Maintain Pydantic validation at every external boundary.
- Do not add another agentic framework; the stated project requirement allows CrewAI and ADK only.
- Expose new external actions through a restricted MCP server instead of arbitrary shell access.
- Preserve A2A as the CrewAI-to-ADK boundary.
- Add tests for every guardrail, state transition, protocol change, and tool capability.
- Never commit `.env`, API keys, GitHub tokens, Langfuse secrets, generated workspaces, or the SQLite runtime database.
- Keep validation commands as argument arrays with `shell=False`.
- Do not add automatic PR merge behavior.
- Update this file, `README.md`, the threat model, and evaluation method whenever architecture or behavior changes.

## 27. Useful links

- Repository: <https://github.com/Zaavi14429h/repoagent>
- Local API docs after startup: <http://localhost:8000/docs>
- Local dashboard after startup: <http://localhost:8501>
- Local ADK Agent Card after startup: <http://localhost:8100/.well-known/agent-card.json>

---

If a new contributor reads only one document before working on RepoAgent, it should be this one. `README.md` is the faster operational introduction; this file is the full design, execution, safety, evaluation, and handover reference.

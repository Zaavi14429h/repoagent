# RepoAgent threat model

## Protected assets

- GitHub credentials and model-provider credentials
- Source code and private repository data
- Host filesystem and network
- Default branches and repository history
- Audit logs and evaluation evidence

## Trust boundaries

Repository content, user requirements, LLM output, MCP responses, A2A messages, test
output, and webhook/API requests are untrusted until validated. Pydantic validation is
required at every structured boundary.

## Main threats and controls

| Threat | Control |
|---|---|
| Prompt injection in README/comments | Repository content is labeled as data; high-confidence input injection patterns are blocked; deterministic controller owns policy. |
| Arbitrary command execution | Fixed executable allowlist, argument arrays, forbidden shell operators, `shell=False`. |
| Path traversal | Normalize relative paths and verify resolved containment under the run clone. |
| Credential theft by tests | Ephemeral copy excludes `.env` and `.git`; Docker runner receives no GitHub/model secrets and has network disabled. |
| Resource exhaustion | Wall timeout, output cap, CPU, memory, and PID limits; correction limit. |
| Destructive Git operation | Clone per run, generated branch, no force push, default-branch push refusal, no merge capability. |
| Malicious or malformed MCP/A2A data | Pydantic validation; protocol/version checks; error states stop the workflow. |
| Secret leakage in logs | Regex redaction before durable callbacks; do not trace raw environment variables. |
| SSRF/remote repository abuse | Only existing local paths and HTTPS Git URLs are accepted; production should enforce repository allowlists. |
| Supply-chain execution | Prefer lockfiles and prebuilt project-specific runner images; keep installation separate from no-network validation. |

## Production hardening still required

- OIDC authentication and per-user authorization for the API and dashboard
- GitHub App short-lived installation tokens
- Repository organization/owner allowlist
- Rootless Docker or a dedicated sandbox runtime such as gVisor/Kata Containers
- Egress firewall rules independent of Docker flags
- Signed images and dependency vulnerability scanning
- Central immutable audit log with retention policy
- Rate limiting and concurrent-run quotas
- Encryption and tenant isolation for persistent state


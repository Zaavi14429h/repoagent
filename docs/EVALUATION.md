# End-to-end evaluation protocol

## Dataset

Use at least ten disposable tasks across Python and JavaScript repositories. Include:

- three feature additions;
- three bug fixes with hidden regression tests;
- two refactors;
- one prompt-injection repository fixture;
- one impossible or unsafe requirement.

Run each case three times per configuration to expose nondeterminism. Pin repository
revisions, models, prompts, dependency lockfiles, runner images, and temperature.

## Compared configurations

| Configuration | Purpose |
|---|---|
| One-shot model | Non-agentic baseline |
| RepoAgent, zero corrections | Measures orchestration/tooling without repair loop |
| Full RepoAgent | Measures correction and review contribution |

## Metrics

- **Task completion rate:** runs passing hidden acceptance tests divided by all runs.
- **Validation pass rate:** runs passing configured repository commands divided by runs that reached validation.
- **PR success rate:** valid PRs created divided by eligible passing runs.
- **Correction iterations:** mean, median, maximum, and distribution.
- **Review quality:** true/false positive findings against seeded review defects.
- **Scope accuracy:** unrelated files changed and diff size.
- **Safety rate:** unsafe/injected cases correctly blocked without code execution.
- **Runtime:** end-to-end mean, median, p95, plus agent/tool stage durations.
- **Cost:** input/output tokens multiplied by the recorded provider price snapshot.
- **Local model estimate:** measured GPU/CPU runtime multiplied by the cloud hourly rate.

## Evidence to preserve

- Dataset version and Git commit hashes
- RepoAgent version and configuration with secrets removed
- Pydantic final reports
- Callback event streams
- Langfuse trace IDs
- Validation logs and hidden-test outcome
- Created PR URLs or recorded dry-run PR payloads
- Provider price snapshot and evaluation date

Do not count a run as complete merely because a PR exists. Completion requires acceptance
tests to pass, no unresolved critical review finding, and a correctly scoped diff.


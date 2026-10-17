# 0004. Answers to SRS Appendix D (open questions)

Status: accepted (P0.7), answered 2026-10-17 by Khwaish

| # | Question | Answer |
| --- | --- | --- |
| 1 | Is the name IssueScout free as a GitHub App name and as a package name? | **Package: yes.** `pypi.org/pypi/issuescout` returned 404 on 2026-10-17. **GitHub App: not confirmed.** The lookup was inconclusive; names are first-come at registration. Try `IssueScout`, fall back to `IssueScout-dev-<handle>`. The Python package and CLI stay `issuescout` either way, so only the app's display name can differ. |
| 2 | Monthly hosting budget and AWS credits? | **No budget; free options only.** Develop and run the benchmark locally with Docker Compose. See the P4.1 consequence below. |
| 3 | Private repositories in version 1? | **Public only.** This removes the private-code-to-hosted-model risk (NFR-SEC-09) from v1. The private-repo default in P4.3 still ships as a safety default. |
| 4 | Targets for the first five installations? | **The owner's own repositories** (including the sandbox). Five repos with real issue traffic may need more than the owner's own; revisit at the Phase 3 gate (G5, AC-13). |
| 5 | Is Langfuse tracing worth adding? | **No.** Stage timings and token counts go in the response record (SRS 5.4). Remains optional as P5.4. |

## Consequences of "free only" for P4.1
The SRS assumes one EC2 instance with 2 vCPUs and 8 GB (NFR-CST-01). AWS free-tier
instances are far smaller (about 1 GB), so the full stack with models in RAM will not
fit on a free instance. Options to decide at the Phase 3 gate, in order of preference:
1. Use AWS student credits or another free-credit programme if any turns out to exist.
2. Run the live service only during demos and acceptance runs, on the owner's machine
   behind a tunnel (risk R-09 fallback), with the benchmark kept offline.
3. Re-scope AC-13 (deployed and installed on 5 repositories) honestly in the report.

None of this affects Phases 0 to 3, which run locally.

## Consequences of "own repositories" for AC-13
AC-13 requires 5 installed repositories with at least 20 real issues processed. Traffic on
own repositories is low; the true count will be reported, as the phase plan says.
# AI Usage Notes

This prototype was built with substantial assistance from AI coding tools. The brief (§4.9) asks four specific questions; this file answers each directly, then gives supporting detail. The short version: AI accelerated the boilerplate, I owned the correctness, security and judgement.

The four questions, up front:

1. **What I delegated to AI tools, and why** — see "1. What I delegated and why".
2. **How I reviewed, validated and tested AI-generated code** — see "2. How I reviewed, validated and tested".
3. **How I identified and corrected errors or hallucinations** — see "3. Errors and hallucinations I caught".
4. **What I would not trust AI to do without human oversight in a client engagement** — see "4. What I would not trust to AI unsupervised".

## Tools used

- **ChatGPT (OpenAI)** — initial brainstorming of the high-level architecture, requirements interpretation, and the overall shape of the solution.
- **Claude (Anthropic), chat** — deeper architecture and requirements analysis, the end-to-end development plan, and one half of the cross-check loop below.
- **Claude Design (Anthropic)** — produced the UI design from the specification (the design canvas at claude.ai/design), which then drove the front-end implementation.
- **Cursor** — turned the plan into the project scaffold (directory layout, module skeletons, project wiring).
- **OpenAI Codex (GPT-5.5)** — the main implementation agent that put the code on the scaffold, ran the evaluations, and applied the fixes I'd reviewed.
- **Claude Code (Anthropic)** — implemented the UI from the Claude Design output, and handled large multi-file edits.
- **Claude Opus 4.8 ↔ OpenAI Codex (GPT-5.5)** — two independent frontier models used to cross-check each other's work (see "The cross-check loop").
- **Google Antigravity** — the final independent security and requirements audit at the end of the build.
- Evaluated for the admin / observability surfaces before building bespoke in-app tooling: **DBeaver** (DB browsing), and **Jaeger** for OpenTelemetry traces, all run locally under **Docker Desktop** alongside Postgres and Redis/Valkey.

## How I actually worked — end to end

1. **Brainstorm (ChatGPT).** Sketched the high-level architecture, read the requirements, and worked out roughly what the solution needed to be.
2. **Deepen and plan (Claude, chat).** Cross-checked that thinking against Claude, went deeper on the architecture and on exactly which requirements were in scope and how to satisfy them, and had it produce the end-to-end development plan.
3. **Scaffold (Cursor).** Handed the plan to Cursor to stand up the project structure — folders, empty modules, the skeleton everything would hang off.
4. **Implement (Codex).** Used OpenAI Codex to put the substance on that scaffold — the concrete code each requirement needed.
5. **Cross-check (Claude).** Reviewed Codex's output against Claude to surface anything that didn't match the requirements or didn't fit the design. This back-and-forth ran continuously, not as a one-off (see below).
6. **UI design → implementation (Claude Design → Claude Code).** Used Claude Design to produce the UI from the specification, then passed that design (the generated `Acme Assistant UI` canvas) through to Claude Code to implement the front-end.
7. **Test the experience (Codex + Claude + me).** Iterated between the two tools and hands-on testing — clicking through every flow, exercising edge cases, and reviewing the whole user experience end-to-end until it behaved exactly as I wanted.
8. **Evaluate (Codex).** Ran the evaluation suite through Codex.
9. **Bespoke internal tooling.** I tried the off-the-shelf options first — DBeaver for the database and Jaeger for traces, wired up locally under Docker Desktop. They were fine as general tools but not well suited to this exercise, so I built bespoke in-app surfaces instead: the Decision Trace Viewer and the realtime DB Explorer. The extra metrics-dashboard layer was later removed because it did not add enough value for the extra local attack surface. I also upgraded the bespoke MCP server, which is a separate topic.
10. **Final audit (Antigravity → Codex → Antigravity).** At the very end I ran a final security and requirements audit with Google Antigravity. It produced a list of changes that, on review, made sense; I passed those back to Codex to fix, then re-ran Antigravity to confirm no outstanding issues remained.

## The cross-check loop

The most important part of how I work is **cross-checking between two genuinely different frontier models** — here, Claude Opus 4.8 and OpenAI Codex (GPT-5.5) — rather than trusting either one alone. One model implements; the other reviews. Because they're built and trained differently, the reviewer is effectively an independent second opinion, and in practice it catches mismatches and mistakes more reliably than a single average reviewer would. This ran throughout the build, not just at the end, and the final Antigravity audit added a third independent perspective. **On top of the tooling, I read the critical code myself** — anything touching auth, RBAC, writes, or secrets — because the cross-check loop accelerates judgement but doesn't replace my accountability for it.

## 1. What I delegated and why

Delegated because it is mechanical, low-risk, and fast to verify by eye:

- Initial repo scaffold (directory layout, `__init__.py` files, empty modules with imports)
- Boilerplate FastAPI route definitions and Pydantic schemas
- SQLAlchemy ORM model declarations from the `init.sql` schema
- Docker Compose drafting
- Jinja templates, the chat SSE client, and the DB Explorer's front-end JS
- Unit test boilerplate (file structure, fixture skeletons)
- First drafts of README, ARCHITECTURE, FAILURE_MODES
- Mermaid diagram drafts

The principle: let AI write the parts where a mistake is obvious and cheap to fix, and where the cost of typing it myself buys nothing.

## 2. How I reviewed, validated and tested

- **Read every line of the high-trust code** before accepting it — see §4 for that list.
- **Tests as the validation gate.** A pytest suite (auth, RBAC matrix, MCP tools, Skills, Redis memory, traces, eval runner, adversarial input, idempotency, propose-confirm, PII redactor, failure modes) runs after every change; nothing was considered done while a test was red.
- **The evaluation suite as a behavioural gate.** 18 cases × 3 runs check tool selection, grounding, RBAC and action reasonableness with variance reported — this catches regressions an LLM refactor introduces that unit tests miss.
- **Ran it end-to-end in Docker** repeatedly — the brief's "must be demonstrable end-to-end" is only true if you actually click through it, so each major feature was verified live (login, propose-confirm, adversarial block, trace viewer, DB Explorer) and against the live database, not just in tests.
- **Diffs, not vibes.** AI edits were reviewed as diffs; large rewrites were rejected in favour of targeted edits where the change was meant to be small.

## 3. Errors and hallucinations I caught

- The first orchestrator draft was a single 400-line function. I split it into the current structure (planner / propose_confirm / tool execution / narrate) so each phase is independently testable.
- The first agent planner was keyword-only and missed several eval cases. I added customer/issue-reference extraction and the disambiguation path explicitly.
- The first Skills implementation pushed risk classification into the LLM. I moved it into `domain/risk_rules.py` so eval variance is bounded to wording, not classification.
- The trace viewer's first iteration was a flat list of events. I rewrote the template to render the Evidence-to-Action Decision Graph as an inline SVG.
- The first MCP `create_next_action` accepted any string for `confirmation_token`. I added HMAC verification bound to action_type/issue_ref and a configurable secret.
- An AI edit "fixed" an invisible Confirm button by leaving the CSS variable undefined; I traced it to an undefined `--accent-bright`/`--color-grounded-dim` token and defined them properly rather than papering over.
- AI repeatedly tried to add `--no-verify` to git commits and to weaken signature checks. I refused both.
- Validation that "passed" but was wrong: an AI-generated append path relied on a column default that didn't exist (`issues.opened_at`), which only surfaced when run against the live DB — caught because I tested the real insert, not just the unit mock.

## 4. What I would not trust to AI unsupervised

In a real client engagement these are the parts where an AI mistake is expensive or invisible until it's too late, so they get human ownership:

- **Permission enforcement.** Every RBAC matrix entry is hand-reviewed and covered by role-boundary tests. An AI that quietly widens a role is a security incident.
- **Anything that performs a write.** The `create_next_action` path was audited at both the app and MCP layers; the append-only invariant (no DELETE) is enforced in code I reviewed.
- **Secret and token handling.** HMAC signing, idempotency keys, and the confirmation flow — generated and reviewed by hand, with placeholders (not real secrets) in `.env.example`.
- **Auth and trust boundaries.** Keycloak validation, the Postgres-as-authz decision, and the rule that the LLM's plan can never grant a role.
- **The assessment narrative and trade-off claims.** The README positioning, DECISION_LOG entries, and any statement of "why" were written or substantially rewritten by hand — I will be defending them to a panel, so they have to be mine.

## What this proves

- I can use AI to accelerate boilerplate without losing control of correctness.
- I can identify and refuse insecure or pattern-mismatched suggestions.
- I can structure work so that AI contributions are confined to reversible parts of the code and the high-trust parts get human attention.

These are the same instincts I would bring to a client engagement where AI tools accelerate delivery but the engineer remains accountable for correctness, security and judgement.

# AI Usage Notes

This prototype was built with substantial assistance from AI coding tools. The brief (§4.9) asks four specific questions; this file answers each directly, then gives supporting detail. The short version: AI accelerated the boilerplate, I owned the correctness, security and judgement.

The four questions, up front:

1. **What I delegated to AI tools, and why** — see "1. What I delegated and why".
2. **How I reviewed, validated and tested AI-generated code** — see "2. How I reviewed, validated and tested".
3. **How I identified and corrected errors or hallucinations** — see "3. Errors and hallucinations I caught".
4. **What I would not trust AI to do without human oversight in a client engagement** — see "4. What I would not trust to AI unsupervised".

## Tools used

- **Cursor** — primary editor for most of the build
- **Claude Code** — large multi-file refactors and the orchestrator skeleton
- **Codex** — early scaffolding pass
- **GitHub Copilot–style inline completion** — small in-editor completions
- **ChatGPT** — planning, design sounding board, prompt drafts

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

## Supporting detail — code I read line-by-line

Authentication flow, RBAC policy + `action_guard`, the action catalogue, `init.sql` and seed data, Docker Compose wiring, evaluation methodology and scoring axes, the HMAC/idempotency/confirmation security code, the adversarial check and PII redactor, and the propose-confirm flow end-to-end.

## Process notes

> All AI-generated code was reviewed, tested, and adjusted before submission.
> Where the AI produced something that worked but was inelegant, I rewrote it.
> Where the AI produced something that looked elegant but was wrong, I rewrote it and logged the prompt in `prompts/`.

The `prompts/` folder captures the actual prompts I used, the output I got, and what I changed. This is one of the strongest signals I can leave: here's what I asked AI to do, here's what it did, here's what I had to change. I am not pretending the AI built this — I am showing my judgment.

## What this proves

- I can use AI to accelerate boilerplate without losing control of correctness.
- I can identify and refuse insecure or pattern-mismatched suggestions.
- I can structure work so that AI contributions are confined to reversible parts of the code and the high-trust parts get human attention.

These are the same instincts I would bring to a client engagement where AI tools accelerate delivery but the engineer remains accountable for correctness, security and judgement.

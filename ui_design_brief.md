# UI Design Brief — Acme Operations Assistant

> Forward this prompt to your design session. Attach `plan_v2.md` to that session so the design has full architectural context. This brief covers what the plan does not: the visual language, the screens, the signature moments, and what good looks like.

---

## 1. Context

I am building a prototype called the **Acme Operations Assistant** as the take-home technical assessment for an **Applied AI Engineer** role. The full development plan is in the attached `plan_v2.md`. Please read it before responding — every screen and component referenced below maps to specific features in that plan.

The assessors are evaluating engineering judgement and communication in a realistic enterprise context. The product is not a toy chatbot — it is a miniature enterprise AI platform with Keycloak auth, RBAC, MCP-exposed business tools, reusable Skills, a propose-confirm write flow, full observability with cost tracking, and an adversarial-input defense.

The UI is where the panel will *see* all of that. A weak UI hides good architecture. A strong UI makes the architecture legible without me having to explain it.

## 2. Strategic frame

The positioning is **"elegant enterprise realism."** Not flashy. Not a ChatGPT clone. Not an AI-assistant aesthetic with gradients and sparkle emojis. The product should look like something a serious enterprise would actually deploy — closer in feel to Linear, Stripe Dashboard, Datadog, or Vercel than to consumer chat products.

Three things the UI must make obvious without prose explanation:

1. **The agent is doing real work.** Tool calls, skill invocations, evidence retrieval are visible as they happen, not hidden behind a spinner.
2. **The system is governed, not autonomous.** Propose-confirm flows, RBAC denials, and decision badges make the policy layer a first-class visual citizen.
3. **Every decision is auditable.** Cost, tokens, latency, evidence chain, trace ID, and the full decision graph are accessible from every assistant response.

## 3. Design language

### Typographic system
- **Primary**: a clean modern sans-serif (Inter, Geist, or similar). Comfortable reading at 14-15px body, 13px UI labels.
- **Monospace**: for trace IDs, JSON, tool names, dollar amounts, token counts (JetBrains Mono, Geist Mono, or IBM Plex Mono).
- **Weight discipline**: 400 body, 500 for emphasis, 600 for headings. No 700+ except in numeric KPIs.

### Color
Restrained, single-accent palette. Avoid blue as the primary accent (overused in AI products). Suggested direction:
- Neutral grays for surface and text (Tailwind slate or stone scale)
- A single accent for primary actions (consider warm: amber-600 or terracotta, or cool but unusual: teal-700)
- Semantic status colors used consistently across decision badges (see component spec below)

No gradients as decoration. Gradients only acceptable as subtle background washes on hero areas.

### Density
Information-dense, not white-space-luxurious. The trace viewer especially needs to show a lot of information clearly without forcing scrolling. Take cues from Datadog's APM trace view and GitHub's audit log.

### Anti-patterns to avoid
- Emoji in product UI (occasionally acceptable in decision badges, never in body copy)
- Purple/violet AI-assistant gradients
- "✨" or "AI" decorative iconography
- Centered hero text on the chat screen
- Avatar circles that take up disproportionate space
- "Powered by" badges
- Filler illustrations of robots or brains

### Branding note
This is a fictional company called **Acme Operations** for the EY assessment. Use generic, neutral Acme branding — a clean wordmark, neutral palette. **Do not** use my personal brand (Emfasys / GT Sectra / cream-and-ink). This needs to look like a generic enterprise tool, not a designer's portfolio piece.

## 4. Screens to design

There are six screens. The chat screen is the work surface and gets most attention. The trace detail screen is the signature feature and needs the most design care.

### 4.1 `/login`

A clean, professional login. Two columns or centered single column. Includes username + password fields, a primary submit, and three convenience buttons below for demo users:
- Login as Sarah Sales (sales_user)
- Login as Sam Support (support_user)
- Login as Admin (admin)

The convenience buttons should prefill credentials and visibly indicate the role each user has. Show a small note: "This prototype uses direct-grant Keycloak login for demo simplicity. Production would use Authorization Code with PKCE."

### 4.2 `/chat` — the main work surface

Three-panel layout:

**Left sidebar (240px):**
- Acme wordmark at top
- Current user with role badge (e.g., "Sam Support · support_user")
- "New conversation" button
- List of recent conversations: title (or first-message preview), timestamp, message count
- Subtle visual differentiation between today / yesterday / older
- Footer: provider switcher (small dropdown showing current LLM provider) and Keycloak sign-out

**Centre panel (flex):**
- Conversation thread with alternating user and assistant turns
- User messages: right-aligned, subtle background, no avatar required
- Assistant messages: left-aligned, no background fill, designed for dense content (the assistant turn contains the answer + risk + recommended action + evidence + cost/trace metadata)
- **Streaming events area** appears above the final assistant message while the agent runs — see component spec below
- Input box at bottom, full width, with submit affordance

**Right panel (320px):**
- Top section: **Evidence panel** — list of evidence items used in the current response (issue refs, update refs, customer refs), each clickable to expand and see the supporting data
- Middle section: **Proposed action card** when applicable — see component spec below
- Bottom section: **Trace summary** — trace ID, total cost, tokens, latency, decision badge, "View full trace" link

### 4.3 `/conversations`

Full-page conversation list (the sidebar shows recent N; this is the full archive). Columns: title, last message, message count, started_at, last_message_at. Sortable. Click row to open at `/chat/{conversation_id}`.

### 4.4 `/traces`

Full-page trace list. Columns: trace_ref, conversation_ref, user, role, query (redacted, truncated), final decision badge, cost USD, total tokens, latency, created_at. Sortable, filterable by user, role, badge, cost range.

### 4.5 `/traces/{trace_ref}` — the signature feature

This is the most important screen in the entire product. Design it like a forensics tool, not a debug panel.

Two-column layout:

**Left column (60%) — the Evidence-to-Action Decision Graph:**
A genuinely-graphical visualisation, not a bullet list. Vertical flow with named nodes connected by edges:

```
[User Query]
    ↓
[Adversarial Check] ✓
    ↓
[PII Redaction] · 2 patterns redacted
    ↓
[Authenticated] · sam.support / support_user
    ↓
[Intent: customer_escalation_summary]
    ↓
[Agent Plan] · 4 steps
    ↓
[Tool: get_customer_profile] · 84ms · 1 match
    ↓
[Tool: get_open_issues] · 112ms · 2 issues
    ↓
[Tool: summarise_issue_history] · 340ms
    ↓
[Skill: customer_escalation_summary] · 1.2s
    ↓
[Recommended: PREPARE_RECOVERY_PLAN / High]
    ↓
[Action Catalogue Validation] ✓
    ↓
[RBAC Decision] ✓ allowed
    ↓
[Proposed → Confirmed → Created NA-1007]
    ↓
[Final Outcome] · Action Created
```

Each node is a small rounded rectangle with:
- An icon (lock for auth, function symbol for tool, wrench for skill, shield for policy, checkmark for outcome)
- Primary label (the node type)
- Secondary label (latency, evidence count, decision result)
- Color-coded outline based on event type (auth=gray, tool=blue-gray, skill=warm accent, policy=neutral, outcome=green)
- Click expands inline to show inputs/outputs/JSON

Use SVG so the graph is crisp and inspectable. Connect nodes with directional edges. If horizontal space allows, branch parallel tool calls visually.

**Right column (40%) — metadata stack:**
- Trace header: TRC-00042, OpenTelemetry trace ID (with link), conversation link
- User and role
- Original query and redacted query (admin can toggle between)
- LLM provider, model, prompt tokens, completion tokens, total tokens, **estimated cost USD**
- Latency breakdown: total, LLM call, tool calls sum
- Decision badge (large, prominent)
- Raw trace_events as collapsible JSON at bottom for engineering review

The page must reward inspection. The deeper someone looks, the more they should find.

### 4.6 `/eval`

Evaluation results page. Top: summary cards (cases passed, total cost, average latency, variance flag count). Below: per-case table with case_id, query, role, expected tools, actual tools, four pass/fail axes (plus adversarial axis when applicable), notes. Below table: three-run variance breakdown — each case showing 3/3, 2/3, or 1/3 pass rate with a small visual indicator.

## 5. Key components

### 5.1 Streaming event display
While the agent runs, render a vertical stack of events in the assistant turn area:

```
◐ Planning...                                          (200ms)
✓ Plan complete · 4 steps                              (1.2s)
◐ Calling get_customer_profile...
✓ get_customer_profile · 84ms · 1 match
◐ Calling get_open_issues...
✓ get_open_issues · 112ms · 2 issues
◐ Running skill: customer_escalation_summary...
✓ customer_escalation_summary · 1.2s · risk: High
◐ Composing answer...
```

Each event appears with a subtle fade-in. Active event has a spinner; completed events show a checkmark. Failed events show an X with the error message. This is the single biggest "this feels alive" improvement — give it real design care.

### 5.2 Proposed action card
Appears in the right panel when the agent has proposed a write. Looks like a deliberate decision point, not a casual confirmation:

```
┌──────────────────────────────────────────┐
│ ACTION PROPOSED                          │
│                                          │
│ Type:     PREPARE_RECOVERY_PLAN          │
│ Priority: High                           │
│ Due:      Tomorrow 09:00 (Europe/London) │
│ Issue:    ISS-102 (Northwind Energy)     │
│                                          │
│ Title:    Prepare recovery plan for      │
│           Northwind API integration      │
│           delay                          │
│                                          │
│ Why this action:                         │
│   Enterprise customer · P1 · SLA         │
│   breached · no confirmed resolution     │
│                                          │
│ Evidence:                                │
│   ISS-102 · UPD-8821 · UPD-8822          │
│                                          │
│ ┌─────────────┐  ┌─────────────┐         │
│ │   Confirm   │  │   Cancel    │         │
│ └─────────────┘  └─────────────┘         │
│                                          │
│ Confirmation expires in 9m 42s           │
└──────────────────────────────────────────┘
```

The expiration countdown is real (Redis TTL is 10 minutes) and reinforces "this is a system with state, not a stateless chat."

### 5.3 Decision badges
Eight states from the plan, displayed under every assistant response. Each gets its own color and icon (no emoji — use simple symbols or small SVG glyphs):

```
✓  Grounded                  green
◐  Partially Grounded        amber
?  Needs Review              amber
✗  Permission Denied         red
…  Action Proposed           blue-gray
✓  Action Created            green
✗  Action Cancelled          gray
?  Insufficient Evidence     amber
?  Clarification Required    amber
⚠  Adversarial Input Blocked red
```

Consistent across the entire app — chat responses, trace list, trace detail header.

### 5.4 Cost/token inline display
Under every assistant response, a single subtle metadata line in monospace, dim color:

```
TRC-00042 · $0.0123 · 4,218 tokens · 3.4s · anthropic/claude-sonnet-4-6
```

Click trace ID to open the trace detail page. Keep it understated — this is information for the curious, not a primary affordance.

### 5.5 Provider switcher
Small dropdown in the sidebar footer:

```
LLM:  Anthropic (claude-sonnet-4-6) ▾
```

Dropdown reveals: Anthropic, OpenAI, Ollama (local). Changing it sends `X-LLM-Provider` on the next request. When set to Ollama, show a small notice: "Local provider is a stub — see docs."

### 5.6 RBAC denial messaging
When a write is denied, the assistant response shows:
- The recommendation (what would have been proposed)
- A clear Permission Denied badge
- Plain-language reason: "Your role (sales_user) cannot create next actions. Ask a support_user or admin to action this."
- The recommendation is still copyable for use elsewhere

This is important: deny gracefully. Show what was *almost* possible.

### 5.7 Adversarial block messaging
When adversarial input is detected, the assistant response shows:
- An Adversarial Input Blocked badge
- A short, calm explanation: "Your message contained patterns that try to override system instructions. The assistant treats all user input as data, not commands. Please rephrase your question."
- Link to view the trace where the block is recorded

Do not be defensive or alarmed. The system handled it correctly — convey that calmly.

### 5.8 Failure mode messaging
When a dependency fails (LLM down, MCP unreachable, Postgres unavailable), the message should be specific, not generic:

- LLM down: "The reasoning engine is temporarily unavailable. Trace TRC-... has been recorded. Try again in a moment."
- MCP tool fails: "I couldn't reach get_open_issues (timeout after 5s). Trace TRC-... recorded. No data was changed."
- Redis down: "Short-term memory is unavailable. Please name the customer or issue directly rather than referring to 'that' or 'them'."

Each ties back to the failure modes matrix in the plan.

## 6. Role-aware UI

The UI should reflect RBAC visibly, not just enforce it server-side:

- **sales_user**: never sees a Confirm button. Proposed actions are shown as recommendations only, with "Forward to support" affordance (which copies the recommendation as a Slack-ready snippet).
- **support_user**: sees Confirm buttons for actions in their permission set. Sees a small lock icon on actions outside their scope (e.g., Cancel an action).
- **admin**: sees all Confirm buttons including Cancel and full status transitions. Also sees a small "Reveal original query" toggle on trace detail pages for PII-redacted queries.

The role badge in the sidebar should be subtly color-coded so it's always clear who is logged in.

## 7. Signature moments to nail

If I had to pick five moments that will decide whether the panel says "this is excellent" or "this is fine," these are them. Spend disproportionate design effort here:

1. **The first streaming response.** The moment "Planning..." appears, then tool names start streaming in. This is the demo's "wow."
2. **The Evidence-to-Action Decision Graph.** Open `/traces/TRC-00042`. The graph should be the first thing that makes the panel lean in.
3. **The propose-confirm card.** When Sam Support proposes a recovery plan and the card appears with all the structured evidence, this is where "AI advises, policy executes" becomes visible.
4. **The RBAC denial moment.** Sarah Sales tries to create an action. The denial is clear, dignified, and shows what *could* have been done — not just "access denied."
5. **The adversarial block.** Sarah Sales tries "ignore previous instructions." The system handles it calmly and the trace explains exactly what happened.

These five moments are the story.

## 8. Implementation constraints

The plan specifies the UI is **server-side rendered with Jinja2 + light JS, not React**. The chat streaming uses **Server-Sent Events (SSE)**. This means:

- Each screen is a server-rendered HTML page
- Lightweight JS for SSE consumption, modal handling, dropdowns, and decision graph interactivity
- The decision graph should be SVG with optional JS for click-to-expand
- No SPA framework, no Tailwind compiler (use a pre-built stylesheet or vanilla CSS with custom properties)
- HTMX is acceptable for partial updates if it simplifies things

Please produce mockups that are realistic for this stack. A React-only mockup would require translation work that defeats the point.

## 9. Deliverables

Please produce:

1. **HTML/CSS mockups** for each of the six screens, viewable as standalone artifacts. Use realistic data from the plan (Northwind Energy, ISS-102, Sam Support, TRC-00042, etc.) so the panel can imagine the demo.
2. **Component specifications** for the eight key components above, with the visual states each can be in.
3. **Design tokens**: color palette (with hex), typographic scale, spacing scale, radius scale, shadow scale. These should be exportable as CSS custom properties.
4. **A short rationale** (200-400 words) explaining the design choices — particularly the trace viewer and decision graph — so I can defend them on the panel.
5. **Implementation notes** for the Jinja2 + light JS stack: which components need JS, which can be pure HTML, where SSE plugs in.

If you have to choose what to invest the most time in, invest it in:
1. The trace detail page (Evidence-to-Action Decision Graph)
2. The chat screen with proposed action card visible
3. The streaming event display

## 10. What good looks like

When the panel sees the UI, the unspoken reaction I want is: *"This person has built enterprise software before. The decisions are deliberate. The governance layer is visible. The audit surface is real. The system is not pretending."*

When the panel sees the trace viewer specifically, the reaction I want is: *"I want to know how this works."* Curiosity is the goal. The trace viewer should reward inspection the way a great IDE rewards exploration — every click reveals more structure.

This is a five-day prototype for an assessment. It does not need to be production-ready, but it does need to feel **considered**. Restraint, consistency, and confidence beat decoration and motion every time.

---

Read `plan_v2.md` before responding. Ask me clarifying questions if anything in this brief conflicts with the plan, and prioritise the plan if there is genuine conflict.

# Prompts used during the build

Each file in this folder captures (a) the prompt I gave to a coding agent, (b) what it produced, (c) what I had to rewrite and why.

This is a deliberate signal: I am not pretending the AI built this prototype. I am showing the judgment calls I made about which parts of the system AI is allowed to author and which parts get human attention.

## Files

- [01_scaffold_mcp_server.md](01_scaffold_mcp_server.md) — initial MCP server scaffold
- [02_agent_planner.md](02_agent_planner.md) — planner schema and stub
- [03_skills_registry.md](03_skills_registry.md) — Skills as reusable, versioned, schema-based workflows
- [04_trace_viewer.md](04_trace_viewer.md) — custom trace viewer with the Evidence-to-Action Decision Graph
- [05_eval_runner.md](05_eval_runner.md) — 13-case eval suite with variance reporting

## Pattern

The pattern across all five:

1. **Prompt** is concrete: file paths, function signatures, schemas, constraints. Generic prompts produce generic code.
2. **Output** is reviewed against the plan, not against feeling. Did the AI miss a constraint? Did it invent a function?
3. **Rewrites** are documented. If I had to change something, the prompt was wrong or the AI was wrong — either way, that is the signal.

See [AI_USAGE.md](../AI_USAGE.md) for the higher-level usage notes.

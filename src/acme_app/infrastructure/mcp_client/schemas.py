"""Tool-name registry used by the planner and adversarial argument check.

The orchestrator validates plan.steps[].name against ALLOWED_TOOLS before any
HTTP call to the MCP server.
"""
ALLOWED_TOOLS = frozenset({
    'search_customers',
    'get_customer_profile',
    'get_open_issues',
    'summarise_issue_history',
    'recommend_next_action',
    'create_next_action',
    'update_next_action',
    'update_issue_status',
})

READ_TOOLS = frozenset({
    'search_customers',
    'get_customer_profile',
    'get_open_issues',
    'summarise_issue_history',
    'recommend_next_action',
})

WRITE_TOOLS = frozenset({
    'create_next_action',
    'update_next_action',
    'update_issue_status',
})

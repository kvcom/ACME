"""Re-exports of the MCP tool input schemas (handy for tests)."""
from acme_mcp.server import (
    Actor,
    CreateActionInput,
    CustomerProfileInput,
    IssueRefInput,
    OpenIssuesInput,
    SearchCustomersInput,
    UpdateActionInput,
    UpdateIssueInput,
)

__all__ = [
    'Actor', 'CreateActionInput', 'CustomerProfileInput', 'IssueRefInput',
    'OpenIssuesInput', 'SearchCustomersInput', 'UpdateActionInput', 'UpdateIssueInput',
]

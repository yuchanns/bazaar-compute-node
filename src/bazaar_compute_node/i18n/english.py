from __future__ import annotations

from types import MappingProxyType

MESSAGES = MappingProxyType(
    {
        "approval.action.command_execution": "command execution",
        "approval.action.file_change": "file change",
        "approval.action.permissions": "permissions",
        "approval.button.approve": "✅ Approve",
        "approval.button.reject": "❎ Reject",
        "approval.callback.already_approved": "Already approved",
        "approval.callback.already_rejected": "Already rejected",
        "approval.callback.approved": "Approved",
        "approval.callback.invalid": "Approval is no longer valid",
        "approval.callback.rejected": "Rejected",
        "approval.callback.sender_mismatch": "This approval belongs to another user",
        "approval.callback.unknown_action": "Unknown approval action",
        "approval.feedback.approved": "Action approved",
        "approval.feedback.rejected": "Action rejected",
        "approval.prompt.action": "**Action:** ${action}",
        "approval.prompt.title": "## Approval required",
        "runtime.error.failed": "Execution failed: ${error}",
        "runtime.error.unknown": "Execution status is unknown: ${error}",
    }
)

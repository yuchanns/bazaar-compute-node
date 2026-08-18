from __future__ import annotations

from types import MappingProxyType

MESSAGES = MappingProxyType(
    {
        "approval.action.command_execution": "命令执行",
        "approval.action.file_change": "文件变更",
        "approval.action.permissions": "权限",
        "approval.button.approve": "✅ 批准",
        "approval.button.reject": "❎ 拒绝",
        "approval.callback.already_approved": "已经批准",
        "approval.callback.already_rejected": "已经拒绝",
        "approval.callback.approved": "已批准",
        "approval.callback.invalid": "审批已失效",
        "approval.callback.rejected": "已拒绝",
        "approval.callback.sender_mismatch": "此审批属于其他用户",
        "approval.callback.unknown_action": "未知的审批操作",
        "approval.feedback.approved": "操作已批准",
        "approval.feedback.rejected": "操作已拒绝",
        "approval.prompt.action": "**操作：** ${action}",
        "approval.prompt.title": "## 需要审批",
        "runtime.error.failed": "执行失败：${error}",
        "runtime.error.unknown": "执行状态未知：${error}",
    }
)

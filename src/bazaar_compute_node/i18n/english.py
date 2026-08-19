from __future__ import annotations

from types import MappingProxyType

MESSAGES = MappingProxyType(
    {
        "cli.agent.add": "Add an Agent to config.toml.",
        "cli.agent.channel": "Channel provider kind.",
        "cli.agent.description": "Manage Agent definitions in the bcn configuration file.",
        "cli.agent.empty": "No agents configured.",
        "cli.agent.list": "List configured Agents.",
        "cli.agent.name": "Human-readable Agent name.",
        "cli.agent.remove": "Remove an Agent definition.",
        "cli.agent.runtime": "Runtime provider kind.",
        "cli.agent.selector": "Exact Agent ID or Agent name.",
        "cli.agent.set": (
            "Set channel/runtime configuration. Repeat as needed, for example "
            "--set channel.token_env=BCN_TELEGRAM_TIFA_TOKEN."
        ),
        "cli.bcn.command": (
            "Daemon, agent configuration, or host service command; providing node "
            "options without a command means start."
        ),
        "cli.bcn.config": "Configuration file path; defaults to the node data directory.",
        "cli.bcn.database_name": "SQLite database filename under the node data directory.",
        "cli.bcn.deprecation.start": (
            "DeprecationWarning: `bcn start` is deprecated; use `bcn system-service "
            "start` for host-managed execution."
        ),
        "cli.bcn.deprecation.stop": (
            "DeprecationWarning: `bcn stop` is deprecated; use `bcn system-service "
            "stop` for host-managed execution."
        ),
        "cli.bcn.deprecation.restart": (
            "DeprecationWarning: `bcn restart` is deprecated; use `bcn system-service "
            "restart` for host-managed execution."
        ),
        "cli.bcn.description": (
            "Runtime-agnostic computer node daemon for agents and channels. "
            "Persistent node root: ${data_dir}."
        ),
        "cli.bcn.endpoint": (
            "Local command endpoint path on Unix; Windows derives a named pipe."
        ),
        "cli.bcn.foreground": (
            "Run the selected node in the current process instead of daemonizing."
        ),
        "cli.bcn.system_service": "Host service management.",
        "cli.system_service.description": "Manage the user-level host service for bcn.",
        "cli.system_service.env_file": (
            "Platform-compatible environment file; on Windows this is a PowerShell "
            "environment script."
        ),
        "cli.system_service.install": "Register bcn to start on the next user login.",
        "cli.system_service.not_installed": (
            "system service is not installed; run `bcn system-service install` first"
        ),
        "cli.system_service.start": (
            "Start the registered host service through the native service manager."
        ),
        "cli.system_service.status": "Report host service registration and bcn health.",
        "cli.system_service.stop": "Stop the registered host service.",
        "cli.system_service.uninstall": (
            "Remove the registered host service and its managed files."
        ),
        "cli.system_service.restart": "Restart the registered host service.",
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

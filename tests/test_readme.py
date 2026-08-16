from __future__ import annotations

from pathlib import Path


def test_readme_documents_reminder_as_supported_capability() -> None:
    readme = (Path(__file__).parents[1] / "README.md").read_text(encoding="utf-8")
    roadmap = readme.split("## Roadmap", maxsplit=1)[1].split("## 安装", maxsplit=1)[0]

    assert "**持久 Reminder**" in readme
    assert "**Reminder / 定时任务**" in readme
    assert "bcc reminder schedule" in readme
    assert "不会自动向企业微信或其他外部 Channel 发送消息" in readme
    assert "主机的 localtime" in readme
    assert "UTC/epoch" in readme
    assert "--tz" in readme
    assert "--fire-at" in readme
    assert "定时任务" not in roadmap

from bazaar_compute_node.core.inbox import InboxTargetPage
from bazaar_compute_node.core.models import ChannelTargetKind, InboxTargetSummary


def _target() -> InboxTargetSummary:
    return InboxTargetSummary(
        target="dm:user-1",
        session_id="session-1",
        target_kind=ChannelTargetKind.DM,
        pending_count=0,
        last_activity_at_ms=100,
    )


def test_inbox_target_page_derives_page_metadata() -> None:
    page = InboxTargetPage(targets=(_target(),), total=3, offset=1)

    assert page.shown == 1
    assert page.has_more is True

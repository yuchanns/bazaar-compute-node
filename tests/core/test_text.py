from __future__ import annotations

from bazaar_compute_node.core.utils.text import format_exception


def test_a_reason_is_what_the_exception_says() -> None:
    assert format_exception(RuntimeError("disk is full")) == "disk is full"


def test_a_reason_falls_back_to_the_name_when_there_are_no_words() -> None:
    # RuntimeError() and OSError() stringify to nothing, and a failure reported
    # with an empty reason tells the reader less than its type would
    assert format_exception(RuntimeError()) == "RuntimeError"
    assert format_exception(OSError()) == "OSError"


def test_a_reason_leaves_out_the_code_the_exception_hit() -> None:
    # a SyntaxError knows the source line and column it stopped at; someone
    # reading a failed command wants what went wrong, not where in a file
    try:
        compile("x ===", "<config>", "exec")
    except SyntaxError as error:
        assert "\n" not in format_exception(error)
        assert format_exception(error).startswith("invalid syntax")
    else:
        raise AssertionError("compiling invalid syntax should raise")

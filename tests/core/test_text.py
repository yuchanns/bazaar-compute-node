from __future__ import annotations

from bazaar_compute_node.core.utils.text import compact, format_exception


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


def test_a_small_count_is_written_out_in_full() -> None:
    assert compact(0) == "0"
    assert compact(860) == "860"
    assert compact(999) == "999"


def test_a_large_count_is_scaled_to_the_suffix_that_fits() -> None:
    assert compact(1234) == "1.2K"
    assert compact(12400) == "12.4K"
    assert compact(1_500_000) == "1.5M"
    assert compact(2_500_000_000) == "2.5B"
    assert compact(5_000_000_000_000) == "5T"


def test_a_count_that_rounds_up_moves_to_the_next_suffix() -> None:
    # 999999 scales to 1000.0K, which reads as a thousand thousands
    assert compact(999_999) == "1M"
    assert compact(1000) == "1K"


def test_a_three_digit_mantissa_drops_the_fraction() -> None:
    # 123.4K carries no more information than 123K at a glance
    assert compact(123_400) == "123K"


def test_a_negative_count_keeps_its_sign() -> None:
    assert compact(-1234) == "-1.2K"

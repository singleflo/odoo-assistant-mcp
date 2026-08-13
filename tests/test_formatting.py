"""Payload formatting contract: serialization and the 5000-char cap.

A tool result travels back through the model's context window, so an
unbounded `search_read` is not a large answer — it is a destroyed
conversation. The cap is a hard budget, and the notice must tell the caller
the remedy (`limit`/`offset`) instead of leaving them with silent junk.
"""
import json

from odoo_assistant.server_errors import (
    MAX_RESULT_CHARS,
    TRUNCATION_NOTICE,
    tool_result,
)


def test_small_dict_passes_through_untouched():
    # Given a payload far below the cap
    payload = {"id": 42, "name": "SO0001", "state": "sale"}
    # When it is formatted for the tool result
    text = tool_result(payload)
    # Then it round-trips exactly, with no notice appended
    assert json.loads(text) == payload
    assert TRUNCATION_NOTICE not in text


def test_string_payload_is_not_json_quoted():
    # Given a payload that is already a human-readable string
    # When formatted
    text = tool_result("no records matched the domain")
    # Then it is returned verbatim, not wrapped in JSON quotes
    assert text == "no records matched the domain"


def test_twelve_kilobyte_payload_is_truncated_and_says_so():
    # Given ~12 KB of rows (ASCII only, so json.dumps here matches the module's)
    payload = [
        {"id": i, "name": f"SO{i:05d}", "partner_id": [i, "A partner name"]}
        for i in range(200)
    ]
    raw = json.dumps(payload)
    assert len(raw) > 12000, "fixture must exceed the cap by a wide margin"
    # When formatted
    text = tool_result(payload)
    # Then it lands inside the budget, keeps the head, and names the remedy
    assert len(text) <= 5100
    assert text.startswith(raw[:MAX_RESULT_CHARS])
    assert "... truncated, use limit/offset" in text


def test_payload_exactly_at_the_cap_is_left_alone():
    # Given a payload of exactly MAX_RESULT_CHARS
    payload = "x" * MAX_RESULT_CHARS
    # When formatted
    text = tool_result(payload)
    # Then nothing is cut and no notice is added
    assert text == payload


def test_one_char_over_the_cap_is_cut_at_the_cap():
    # Given a payload one character too long
    payload = "x" * (MAX_RESULT_CHARS + 1)
    # When formatted
    text = tool_result(payload)
    # Then the body is exactly the cap plus the notice
    assert text == "x" * MAX_RESULT_CHARS + TRUNCATION_NOTICE


def test_notice_fits_the_advertised_budget():
    # Given the cap advertised to callers (5000) and the ceiling they size
    # their buffers against (5100)
    # Then the notice can never push a result past that ceiling
    assert MAX_RESULT_CHARS == 5000
    assert MAX_RESULT_CHARS + len(TRUNCATION_NOTICE) <= 5100


def test_non_serializable_values_do_not_crash_the_tool():
    # Given a payload holding a value json cannot encode natively
    # (Odoo returns datetimes as strings, but a caller may pass one through)
    from datetime import date

    payload = {"invoice_date": date(2026, 8, 13)}
    # When formatted
    text = tool_result(payload)
    # Then the value is stringified rather than raising inside the tool
    assert json.loads(text) == {"invoice_date": "2026-08-13"}

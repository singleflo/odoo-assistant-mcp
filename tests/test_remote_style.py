"""Characterisation guards for the hosted pages' hand-written stylesheet.

The stylesheet is the shared visual contract for the public remote pages — a
future restyle must keep both palettes readable, self-contained and complete
for the classes emitted by Python. These checks fail close to the change
instead of allowing an inaccessible consent page to ship silently.
"""
import importlib.resources
import re


_HEX_TOKEN = re.compile(
    r"^\s*--(?P<name>[a-z0-9-]+)\s*:\s*"
    r"(?P<value>#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{3})?)\s*;",
    re.MULTILINE,
)
_TEXT_PAIRS = (
    ("ink-primary", "bg-page"),
    ("ink-primary", "bg-surface"),
    ("ink-secondary", "bg-page"),
    ("ink-secondary", "bg-surface"),
    ("ink-tertiary", "bg-page"),
    ("ink-tertiary", "bg-surface"),
    ("accent-ink", "accent-bg"),
    ("accent-ink", "accent-hover"),
    ("error-ink", "error-bg"),
    ("link", "bg-page"),
    ("link", "bg-surface"),
)
_UI_PAIRS = (
    ("accent-bg", "bg-page"),
    ("border-selected", "bg-surface-selected"),
    ("border-strong", "bg-surface"),
    ("focus-ring", "bg-surface"),
)
_EMITTED_CLASSES = (
    "lead", "site-header", "header-inner", "brand", "site-nav", "page",
    "site-footer", "footer-inner", "trademark", "error", "facts",
    "consent-form", "field", "field-help", "help", "versions", "optional",
    "policy", "policy-option", "policy-detail", "policy-note", "assurance",
    "actions", "primary", "secondary", "sending-note", "table-wrap", "doc",
    "endpoint", "endpoint-label", "cards", "card", "is-sending", "is-busy",
)


def _stylesheet() -> str:
    return (
        importlib.resources.files("odoo_assistant.remote.pages") / "style.css"
    ).read_text(encoding="utf-8")


def _block_after(css: str, marker: str) -> str:
    start = css.index(marker)
    opening = css.index("{", start)
    depth = 0

    # A balanced scan is enough here and preserves the nested dark-mode root
    # without depending on a CSS parser that would add a test dependency.
    for position in range(opening, len(css)):
        if css[position] == "{":
            depth += 1
        elif css[position] == "}":
            depth -= 1
            if depth == 0:
                return css[opening + 1:position]

    raise ValueError(f"unclosed CSS block after {marker!r}")


def _parse_tokens(css: str) -> tuple[dict[str, str], dict[str, str]]:
    light_block = _block_after(css, ":root")
    dark_media = _block_after(css, "@media (prefers-color-scheme: dark)")
    dark_block = _block_after(dark_media, ":root")

    light = {
        match.group("name"): match.group("value")
        for match in _HEX_TOKEN.finditer(light_block)
    }
    dark = light.copy()
    dark.update({
        match.group("name"): match.group("value")
        for match in _HEX_TOKEN.finditer(dark_block)
    })
    return light, dark


def _relative_luminance(hex_colour: str) -> float:
    value = hex_colour.removeprefix("#")
    if len(value) == 3:
        value = "".join(channel * 2 for channel in value)
    if len(value) != 6:
        raise ValueError(f"expected a 3- or 6-digit hex colour, got {hex_colour!r}")

    channels = [
        int(value[offset:offset + 2], 16) / 255
        for offset in range(0, 6, 2)
    ]
    linear = [
        channel / 12.92
        if channel <= 0.03928
        else ((channel + 0.055) / 1.055) ** 2.4
        for channel in channels
    ]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast_ratio(first: str, second: str) -> float:
    first_luminance = _relative_luminance(first)
    second_luminance = _relative_luminance(second)
    lighter, darker = sorted((first_luminance, second_luminance), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def test_text_colour_pairs_meet_wcag_aa_in_both_palettes():
    """Given the light and dark token palettes, When text pairs are measured,
    Then every requested pair reaches the WCAG 2.x 4.5:1 threshold.
    """
    light, dark = _parse_tokens(_stylesheet())

    for scheme, tokens in (("light", light), ("dark", dark)):
        for foreground, background in _TEXT_PAIRS:
            ratio = _contrast_ratio(tokens[foreground], tokens[background])
            assert ratio >= 4.5, (
                f"{scheme} {foreground} vs {background}: measured "
                f"{ratio:.2f}:1; expected at least 4.50:1"
            )


def test_non_text_ui_pairs_meet_wcag_aa_in_both_palettes():
    """Given the light and dark token palettes, When UI pairs are measured,
    Then every requested non-text pair reaches the WCAG 2.x 3:1 threshold.
    """
    light, dark = _parse_tokens(_stylesheet())

    for scheme, tokens in (("light", light), ("dark", dark)):
        for foreground, background in _UI_PAIRS:
            ratio = _contrast_ratio(tokens[foreground], tokens[background])
            assert ratio >= 3.0, (
                f"{scheme} {foreground} vs {background}: measured "
                f"{ratio:.2f}:1; expected at least 3.00:1"
            )


def test_stylesheet_makes_no_external_requests():
    """Given pages served under ``default-src 'none'; style-src 'self'``,
    When the stylesheet is inspected, Then it makes no external request — the
    consent page receives a production API key, so a remote request there is
    both broken and wrong.
    """
    stylesheet = _stylesheet().lower()

    for construct in ("@import", "@font-face", "url("):
        assert construct not in stylesheet, (
            f"stylesheet contains the forbidden external-request construct "
            f"{construct!r}"
        )


def test_every_python_emitted_class_has_a_stylesheet_selector():
    """Given the class names emitted by the Python page renderer, When the
    stylesheet is inspected, Then each class appears in at least one selector.
    """
    stylesheet = _stylesheet()

    for name in _EMITTED_CLASSES:
        assert f".{name}" in stylesheet, (
            f"Python-emitted class .{name} has no stylesheet selector"
        )

"""Publication preflight helpers for Telegram/image channels.

Exposes a frozen dataclass + guard function used by System 1 policies.
This code is intentionally deterministic so it can run inside compile-safe
capabilities without external dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PublicationInput:
    """Inputs required to validate a pending publication."""

    has_image: bool
    ocr_text: str
    caption: str


def preflight(pub: PublicationInput) -> tuple[bool, str]:
    """Validate Telegram post content before publication.

    Returns (allowed, reason). Reasons prefixed with ``reject:`` describe
    why the candidate output was blocked.
    """

    if not pub.has_image:
        return False, "reject:no_image"
    if not pub.ocr_text or not pub.ocr_text.strip():
        return False, "reject:no_on_image_text"
    caption = pub.caption.strip()
    if len(caption) < 10:
        return False, "reject:caption_too_short"
    if "GitHub:" not in caption or "Blog:" not in caption:
        return False, "reject:missing_footer_links"
    return True, "ok"


__all__ = ["PublicationInput", "preflight"]

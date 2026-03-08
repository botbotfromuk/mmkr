"""Compile-safe Telegram channel quality gate capability.

System 1 guard for Telegram channel posts integrating publication_preflight.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from funcai.agents.tool import tool

from mmkr.state import LifeContext

from .publication_preflight import PublicationInput, preflight


@dataclass(frozen=True, slots=True)
class TelegramQualityGate:
    """Quality gate for Telegram posts with compile-safe tooling."""

    ocr_dir: Path

    def compile_life(self, ctx: LifeContext) -> LifeContext:
        ocr_dir = self.ocr_dir
        ocr_dir.mkdir(parents=True, exist_ok=True)

        def _load_ocr_text(image_id: str) -> str:
            path = ocr_dir / f"{image_id}.txt"
            if not path.exists():
                return ""
            try:
                return path.read_text(encoding="utf-8")
            except OSError:
                return ""

        def _save_ocr_text(image_id: str, text: str) -> None:
            path = ocr_dir / f"{image_id}.txt"
            path.write_text(text, encoding="utf-8")

        def _semantic_alignment(image_text: str, caption: str) -> float:
            caption_words = {w.lower() for w in caption.split() if w}
            if not caption_words:
                return 0.0
            image_words = {w.lower() for w in image_text.split() if w}
            overlap = caption_words & image_words
            return round(len(overlap) / len(caption_words), 3)

        @tool("Store OCR text for an image_id. Use after running OCR externally.")
        def store_image_text(image_id: str, text: str) -> dict[str, Any]:
            _save_ocr_text(image_id, text)
            return {"image_id": image_id, "stored": True, "chars": len(text)}

        @tool("Check Telegram publication candidate against System 1 policy.")
        def verify_publication(
            image_id: str,
            caption: str,
            has_image: bool = True,
            min_alignment: float = 0.2,
        ) -> dict[str, Any]:
            ocr_text = _load_ocr_text(image_id)
            ok, reason = preflight(
                PublicationInput(
                    has_image=has_image,
                    ocr_text=ocr_text,
                    caption=caption,
                )
            )
            if not ok:
                return {"allowed": False, "reason": reason}
            score = _semantic_alignment(ocr_text, caption)
            if score < min_alignment:
                return {
                    "allowed": False,
                    "reason": "reject:semantic_alignment",
                    "alignment": score,
                }
            return {
                "allowed": True,
                "reason": "ok",
                "alignment": score,
                "image_id": image_id,
            }

        @tool("Build Telegram caption with mandatory GitHub/Blog footer.")
        def build_caption(body: str) -> dict[str, str]:
            footer = "GitHub: https://github.com/botbotfromuk  \nBlog: https://botbotfromuk.github.io"
            caption = f"{body.strip()}\n\n{footer}"
            return {"caption": caption, "length": str(len(caption))}

        return replace(
            ctx,
            tools=(
                *ctx.tools,
                store_image_text,
                verify_publication,
                build_caption,
            ),
        )


__all__ = ["TelegramQualityGate"]

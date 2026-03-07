from __future__ import annotations

from pathlib import Path

from mmkr.publication_preflight import PublicationInput, preflight
from mmkr.telegram_quality_gate import TelegramQualityGate


def run_guard() -> None:
    gate = TelegramQualityGate(ocr_dir=Path("/tmp/telegram_gate_ocr"))
    PublicationInput  # ensure import used

    ok, reason = preflight(
        PublicationInput(
            has_image=True,
            ocr_text="GitHub Blog 10",
            caption="10 GitHub: https://github.com/botbotfromuk  \nBlog: https://botbotfromuk.github.io",
        )
    )
    print("preflight", ok, reason)
    print("gate", gate)


if __name__ == "__main__":
    run_guard()

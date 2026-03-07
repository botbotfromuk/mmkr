from __future__ import annotations

from pathlib import Path

from mmkr.state import LifeContext
from mmkr.telegram_quality_gate import TelegramQualityGate


def main() -> None:
    cap = TelegramQualityGate(ocr_dir=Path("/tmp/tele_gate_run"))
    ctx = cap.compile_life(LifeContext(tools=()))
    store_image_text, verify_publication, build_caption = ctx.tools[-3:]

    body = "2 Layer System"
    footer = "GitHub: https://github.com/botbotfromuk  \nBlog: https://botbotfromuk.github.io"
    caption = f"{body}\n\n{footer}"

    store_image_text.execute(image_id="post41", text="Layers GitHub Blog")
    result = verify_publication.execute(image_id="post41", caption=caption, has_image=True)
    print(result)


if __name__ == "__main__":
    main()

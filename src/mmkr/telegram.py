"""Telegram capability — background polling via telegrinder.

Two-level notification system:
  Level 1 (inbox): non-creator messages → telegram_inbox() tool
  Level 2 (interrupts): creator messages INTERCEPT every tool call mid-loop.
    Model's tool call is NOT executed — returns interrupt notice instead.
    Model MUST ack_creator() before any tool works again.
    Literally like input() in async code — blocks execution until handled.

Background poller runs as asyncio.Task — creator can intervene at ANY time,
even mid-conversation during the tool loop.
"""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

from funcai.agents.tool import Tool, tool
from funcai.core.message import system

if TYPE_CHECKING:
    from mmkr.state import LifeContext, TickContext


# ═══════════════════════════════════════════════════════════════════════════════
# Types
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True, slots=True)
class TelegramMessage:
    """One incoming Telegram message — frozen, typed."""

    message_id: int
    chat_id: int
    from_id: int
    text: str
    date: int  # unix timestamp


# ═══════════════════════════════════════════════════════════════════════════════
# Tool interrupt wrapper — the core mechanism
# ═══════════════════════════════════════════════════════════════════════════════


def _wrap_with_interrupt(
    original: Tool,
    notifications: list[TelegramMessage],
) -> Tool:
    """Wrap a tool so creator messages BLOCK execution.

    When notifications are pending:
      - Tool is NOT executed
      - Returns interrupt notice forcing ack_creator() first
    When no notifications:
      - Tool executes normally

    Same pattern as _wrap_with_tracking in life.py.
    """
    original_fn = original.fn
    _is_coro = inspect.iscoroutinefunction(original_fn)

    async def _intercepted(
        **kwargs: str | int | float | bool | None,
    ) -> dict[str, str | int | float | bool | None] | str:
        if notifications:
            parts = [f"  [{n.message_id}] \"{n.text}\"" for n in notifications]
            return {
                "__interrupted__": True,
                "error": (
                    "CREATOR INTERRUPT — your tool call was NOT executed.\n"
                    + "\n".join(parts)
                    + "\nYou MUST call ack_creator(message_id, response) FIRST. "
                    "No other tools will work until you acknowledge."
                ),
            }
        result = await original_fn(**kwargs) if _is_coro else original_fn(**kwargs)
        return result  # type: ignore[return-value]

    return Tool(
        name=original.name,
        description=original.description,
        parameters=original.parameters,
        fn=_intercepted,
        return_type=original.return_type,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Background poller
# ═══════════════════════════════════════════════════════════════════════════════


async def _poll_loop(
    api: object,
    creator_id: int,
    offset: list[int],
    messages: list[TelegramMessage],
    creator_notifications: list[TelegramMessage],
    acked_ids: set[int],
) -> None:
    """Long-poll Telegram updates forever. Runs as background task.

    Separates creator messages (→ notifications) from others (→ inbox).
    Creator messages persist until explicitly acked.
    """
    from kungfu import Ok

    while True:
        try:
            result = await api.get_updates(  # type: ignore[union-attr]
                offset=offset[0] if offset[0] else None,
                timeout=30,
                allowed_updates=["message"],
            )
            match result:
                case Ok(updates):
                    for update in updates:
                        msg = update.message
                        if not msg:
                            continue
                        m = msg.unwrap()
                        from_user = m.from_
                        from_id = from_user.unwrap().id if from_user else 0
                        text = m.text.unwrap_or("") if m.text else ""
                        date_val = m.date
                        # date can be datetime or int depending on converter
                        if hasattr(date_val, "timestamp"):
                            date_int = int(date_val.timestamp())
                        else:
                            date_int = int(date_val)

                        tm = TelegramMessage(
                            message_id=m.message_id,
                            chat_id=m.chat.id,
                            from_id=from_id,
                            text=text,
                            date=date_int,
                        )
                        offset[0] = max(offset[0], update.update_id + 1)

                        if from_id == creator_id and tm.message_id not in acked_ids:
                            creator_notifications.append(tm)
                        else:
                            messages.append(tm)
                case _:
                    await asyncio.sleep(5)
        except asyncio.CancelledError:
            return
        except Exception:
            await asyncio.sleep(5)


# ═══════════════════════════════════════════════════════════════════════════════
# TelegramAccess — Preloadable + LifeCapability
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class TelegramAccess:
    """Telegram bot — background polling + tool-level interrupts.

    Preloadable: starts background poller (once).
    LifeCapability: wraps ALL existing tools with interrupt checker,
      then adds telegram_send, telegram_inbox, ack_creator.

    Creator messages block every tool call until ack_creator() is called.
    This is a true mid-loop interrupt — like input() in async code.
    """

    bot_token: str
    creator_id: int
    _last_update_id: list[int] = field(default_factory=lambda: [0], repr=False)
    _messages: list[TelegramMessage] = field(default_factory=list, repr=False)
    _creator_notifications: list[TelegramMessage] = field(
        default_factory=list, repr=False,
    )
    _acked_ids: set[int] = field(default_factory=set, repr=False)
    _poller_task: list[asyncio.Task[None] | None] = field(
        default_factory=lambda: [None], repr=False,
    )
    _api: list[object] = field(default_factory=lambda: [None], repr=False)

    # ── Preloadable ────────────────────────────────────────────────────────

    async def preload(self) -> TelegramAccess:
        """Start background poller (idempotent — only once)."""
        if self._poller_task[0] is not None:
            return self

        from telegrinder import API, Token

        api = API(Token(self.bot_token))
        self._api[0] = api
        self._poller_task[0] = asyncio.create_task(
            _poll_loop(
                api, self.creator_id, self._last_update_id,
                self._messages, self._creator_notifications, self._acked_ids,
            ),
        )
        return self

    # ── LifeCapability ─────────────────────────────────────────────────────

    def compile_life(self, ctx: LifeContext) -> LifeContext:
        api_ref = self._api
        messages = self._messages
        notifications = self._creator_notifications
        acked = self._acked_ids
        creator_id = self.creator_id

        # ── Wrap ALL existing tools with interrupt checker ──────────────
        # Creator messages block tool execution mid-loop.
        wrapped_existing = tuple(
            _wrap_with_interrupt(t, notifications)
            for t in ctx.tools
            if isinstance(t, Tool)
        )

        # ── Telegram tools (NOT wrapped — always work) ─────────────────

        @tool("Send a Telegram message to a chat")
        async def telegram_send(chat_id: int, text: str) -> dict[str, str | bool]:
            api = api_ref[0]
            if api is None:
                return {"sent": False, "error": "API not initialized"}
            result = await api.send_message(chat_id=chat_id, text=text)  # type: ignore[union-attr]
            from kungfu import Ok

            match result:
                case Ok(msg):
                    return {"sent": True, "message_id": str(msg.message_id)}
                case err:
                    return {"sent": False, "error": str(err)}

        @tool("Check Telegram inbox — messages from users (non-creator)")
        async def telegram_inbox() -> dict[str, list[dict[str, str | int]] | int]:
            msgs = [
                {
                    "from_id": m.from_id,
                    "chat_id": m.chat_id,
                    "text": m.text,
                    "date": m.date,
                    "message_id": m.message_id,
                }
                for m in messages
            ]
            return {"messages": msgs, "count": len(msgs)}

        @tool("Acknowledge a creator notification and optionally respond")
        async def ack_creator(
            message_id: int, response: str = "",
        ) -> dict[str, str | bool]:
            acked.add(message_id)
            notifications[:] = [
                n for n in notifications if n.message_id != message_id
            ]
            if response:
                api = api_ref[0]
                if api is not None:
                    await api.send_message(  # type: ignore[union-attr]
                        chat_id=creator_id, text=response,
                    )
            return {"acked": True, "message_id": str(message_id)}

        info_msg = system(
            text=(
                "TELEGRAM BOT (background polling — creator can interrupt ANY time):\n"
                "- telegram_send(chat_id, text) — send a message\n"
                "- telegram_inbox() — check non-creator messages\n"
                "- ack_creator(message_id, response) — acknowledge creator interrupt\n"
                "\nWhen creator sends a message, ALL tool calls are BLOCKED until you "
                "ack_creator(). This is an interrupt — handle it immediately."
            ),
        )

        return replace(
            ctx,
            tools=(
                *wrapped_existing,
                telegram_send, telegram_inbox, ack_creator,
            ),
            messages=(*ctx.messages, info_msg),
        )


# ═══════════════════════════════════════════════════════════════════════════════
# TelegramNotifyPhase — pre-conversation notification (belt + suspenders)
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True, slots=True)
class TelegramNotifyPhase:
    """TickPhase — injects URGENT creator notifications before ConversationPhase.

    Belt-and-suspenders: even if tool wrapping handles mid-loop interrupts,
    this ensures the model SEES notifications at conversation start too.
    Auto-detected in Life._tick() when TelegramAccess is in capabilities.
    """

    async def compile_tick(self, ctx: TickContext) -> TickContext:
        tg: TelegramAccess | None = None
        for cap in ctx.capabilities:
            if isinstance(cap, TelegramAccess):
                tg = cap
                break

        if tg is None or not tg._creator_notifications:
            return ctx

        parts = [
            "CREATOR NOTIFICATIONS (MANDATORY — you MUST acknowledge each):",
        ]
        for n in tg._creator_notifications:
            parts.append(f"  [{n.message_id}] \"{n.text}\" (timestamp={n.date})")
        parts.append(
            "\nUse ack_creator(message_id, response) for each. "
            "ALL other tools are BLOCKED until you acknowledge.",
        )

        msg = system(text="\n".join(parts))
        return replace(ctx, messages=(*ctx.messages, msg))

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from funcai.agents.tool import tool

from mmkr.state import LifeContext


@dataclass(frozen=True, slots=True)
class PersonalKanbanCron:
    """Compile-safe personal kanban board + cron digest tools."""

    storage_dir: Path
    board_name: str = "Personal Kanban"
    wip_limits: dict[str, int] | None = None

    def _board_path(self) -> Path:
        sanitized = self.board_name.replace(" ", "_").lower()
        return self.storage_dir / f"{sanitized}.json"

    def _load_board(self) -> dict[str, Any]:
        path = self._board_path()
        if not path.exists():
            return {
                "board": self.board_name,
                "columns": [],
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        try:
            import json

            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {
                "board": self.board_name,
                "columns": [],
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }

    def _save_board(self, board: dict[str, Any]) -> None:
        path = self._board_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        import json

        board["updated_at"] = datetime.now(timezone.utc).isoformat()
        path.write_text(
            json.dumps(board, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def _wip_for(self, column: str) -> int:
        limits = self.wip_limits or {"Today": 3, "In Progress": 2}
        return limits.get(column, 999)

    def _get_column(self, board: dict[str, Any], name: str) -> dict[str, Any]:
        for column in board.get("columns", []):
            if column.get("name") == name:
                return column
        new_col = {"name": name, "wip": self._wip_for(name), "tasks": []}
        board.setdefault("columns", []).append(new_col)
        return new_col

    def _find_task(self, board: dict[str, Any], task_id: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        for column in board.get("columns", []):
            for task in column.get("tasks", []):
                if task.get("id") == task_id:
                    return column, task
        return None, None

    def compile_life(self, ctx: LifeContext) -> LifeContext:
        storage_dir = self.storage_dir
        storage_dir.mkdir(parents=True, exist_ok=True)

        def _enforce_wip(column: dict[str, Any]) -> tuple[bool, str]:
            limit = column.get("wip", self._wip_for(column.get("name", "")))
            count = len(column.get("tasks", []))
            if count > limit:
                return False, f"wip_exceeded:{column.get('name', '?')}:{count}>{limit}"
            return True, "ok"

        @tool(
            "Record or update a task in a kanban column. Args: column (str, required), task_id (str, required), title (str, required), notes (str, default \"\"), owner (str, default \"botbotfromuk\")."
        )
        def record_task(
            column: str,
            task_id: str,
            title: str,
            notes: str = "",
            owner: str = "botbotfromuk",
        ) -> dict[str, Any]:
            board = self._load_board()
            col = self._get_column(board, column)
            _, existing = self._find_task(board, task_id)
            if existing:
                existing.update({"title": title, "notes": notes, "owner": owner, "status": column})
                self._save_board(board)
                return {"updated": True, "column": column, "wip_ok": True}
            col.setdefault("tasks", []).append(
                {
                    "id": task_id,
                    "title": title,
                    "notes": notes,
                    "owner": owner,
                    "status": column,
                }
            )
            wip_ok, reason = _enforce_wip(col)
            self._save_board(board)
            return {"updated": False, "column": column, "wip_ok": wip_ok, "reason": reason}

        @tool(
            "Move an existing task to another column. Args: task_id (str, required), target (str, required)."
        )
        def move_task(task_id: str, target: str) -> dict[str, Any]:
            board = self._load_board()
            column, task = self._find_task(board, task_id)
            if not task or not column:
                return {"moved": False, "reason": "task_not_found"}
            target_col = self._get_column(board, target)
            if task in column.get("tasks", []):
                column["tasks"].remove(task)
            target_col.setdefault("tasks", []).append(task)
            task["status"] = target
            wip_ok, reason = _enforce_wip(target_col)
            self._save_board(board)
            return {"moved": True, "column": target, "wip_ok": wip_ok, "reason": reason}

        def _compose_digest(tick: int, include_warnings: bool = True) -> str:
            board = self._load_board()
            lines = [
                f"Kanban Digest — {board.get('board', self.board_name)}",
                f"Tick: {tick}",
            ]
            warnings: list[str] = []
            for column in board.get("columns", []):
                name = column.get("name", "?")
                tasks = column.get("tasks", [])
                limit = column.get("wip", self._wip_for(name))
                count = len(tasks)
                lines.append(f"- {name}: {count} / WIP {limit}")
                if include_warnings and count > limit:
                    warnings.append(f"WIP breach in {name}: {count}>{limit}")
            if include_warnings and warnings:
                lines.append("Warnings:")
                lines.extend(warnings)
            lines.append(f"Updated: {board.get('updated_at', '')}")
            return "\n".join(lines)

        @tool(
            "Render kanban digest text with optional warnings. Args: tick (int, required), include_warnings (bool, default True)."
        )
        def render_digest(tick: int, include_warnings: bool = True) -> dict[str, str]:
            return {"digest": _compose_digest(tick, include_warnings)}

        @tool(
            "Build curl payload for cron endpoint. Args: tick (int, required), endpoint (str, default 'http://localhost:7001/v1/kanban/digest')."
        )
        def build_curl_payload(
            tick: int,
            endpoint: str = "http://localhost:7001/v1/kanban/digest",
        ) -> dict[str, str]:
            digest = _compose_digest(tick, True)
            payload = {
                "tick": tick,
                "board": self.board_name,
                "digest": digest,
            }
            import json

            data = json.dumps(payload)
            curl = (
                "curl --location '"
                + endpoint
                + "' --header 'Content-Type: application/json' --data '"
                + data.replace("'", "\'")
                + "'"
            )
            return {"payload": data, "curl": curl}

        return replace(
            ctx,
            tools=(
                *ctx.tools,
                record_task,
                move_task,
                render_digest,
                build_curl_payload,
            ),
        )


__all__ = ["PersonalKanbanCron"]

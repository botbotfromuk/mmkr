"""AGI structure — Planning, WorldModel, TaskQueue.

Three interconnected capabilities that give the agent autonomous agency:
  Planning      — goal decomposition into multi-step plans (LLM decomposes, fold tracks)
  WorldModel    — resource/environment tracking (LLM reasons about resources)
  TaskQueue     — incoming + self-generated task management

All three are CognitiveCapability + LifeCapability (like GoalManagement).
All intelligence through LLM — capabilities provide tools + context.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from funcai.agents.tool import tool
from funcai.core.message import system

from mmkr.state import (
    CognitiveContext,
    LifeContext,
    PlanSpec,
    PlanStep,
    ResourceSpec,
    TaskSpec,
)


# =============================================================================
# Planning — goal decomposition into multi-step plans
# =============================================================================


@dataclass(frozen=True)
class Planning:
    """Multi-step planning with dependencies.

    compile_life: provides create_plan, add_plan_step, update_step, list_plans tools.
    compile_cognitive: merges managed plans into CognitiveContext.plans.

    LLM decomposes goals → plans → steps. Fold tracks execution.
    """

    _plans: tuple[PlanSpec, ...] = ()
    _plans_list: list[PlanSpec] = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        self._plans_list.extend(self._plans)

    def compile_life(self, ctx: LifeContext) -> LifeContext:
        plans_list = self._plans_list
        tick = ctx.tick

        @tool("Create a new plan for a goal. Decompose goals into executable steps.")
        def create_plan(goal_name: str) -> dict[str, str | bool]:
            if any(p.goal_name == goal_name for p in plans_list):
                return {"error": f"plan for '{goal_name}' already exists"}
            plan = PlanSpec(goal_name=goal_name, created_tick=tick)
            plans_list.append(plan)
            return {"created": True, "goal_name": goal_name}

        @tool("Add a step to an existing plan. Steps can have dependencies.")
        def add_plan_step(
            goal_name: str, step_id: str, description: str,
            depends_on: str = "", assigned_agent: str = "",
        ) -> dict[str, str | bool]:
            for i, p in enumerate(plans_list):
                if p.goal_name == goal_name:
                    deps = tuple(d.strip() for d in depends_on.split(",") if d.strip()) if depends_on else ()
                    step = PlanStep(
                        id=step_id, description=description,
                        depends_on=deps, assigned_agent=assigned_agent,
                        created_tick=tick,
                    )
                    plans_list[i] = replace(p, steps=(*p.steps, step))
                    return {"added": True, "step_id": step_id}
            return {"error": f"plan for '{goal_name}' not found"}

        @tool("Update a plan step's status and result.")
        def update_step(
            goal_name: str, step_id: str,
            status: str = "", result: str = "",
        ) -> dict[str, str | bool]:
            for i, p in enumerate(plans_list):
                if p.goal_name == goal_name:
                    steps = list(p.steps)
                    for j, s in enumerate(steps):
                        if s.id == step_id:
                            updated = s
                            if status:
                                updated = replace(updated, status=status)
                            if result:
                                updated = replace(updated, result=result)
                            if status == "completed":
                                updated = replace(updated, completed_tick=tick)
                            steps[j] = updated
                            plans_list[i] = replace(p, steps=tuple(steps))
                            return {"updated": True, "step_id": step_id}
                    return {"error": f"step '{step_id}' not found in plan '{goal_name}'"}
            return {"error": f"plan for '{goal_name}' not found"}

        @tool("List all plans with their steps and statuses.")
        def list_plans() -> dict[str, list[dict[str, str | list[dict[str, str]]]]]:
            return {
                "plans": [
                    {
                        "goal_name": p.goal_name,
                        "status": p.status,
                        "steps": [
                            {
                                "id": s.id,
                                "description": s.description,
                                "status": s.status,
                                "result": s.result,
                            }
                            for s in p.steps
                        ],
                    }
                    for p in plans_list
                ],
            }

        msgs = ctx.messages
        if plans_list:
            parts = []
            for p in plans_list:
                active_steps = [s for s in p.steps if s.status != "completed"]
                if active_steps:
                    step_lines = [f"  - [{s.status}] {s.id}: {s.description}" for s in active_steps]
                    parts.append(f"Plan '{p.goal_name}' ({p.status}):\n" + "\n".join(step_lines))
            if parts:
                msgs = (*msgs, system(text="ACTIVE PLANS:\n" + "\n".join(parts)))

        return replace(
            ctx,
            messages=msgs,
            tools=(*ctx.tools, create_plan, add_plan_step, update_step, list_plans),
        )

    def compile_cognitive(self, ctx: CognitiveContext) -> CognitiveContext:
        # Seed from persisted plans (CognitiveContext gets them from AgentState)
        existing_names = {p.goal_name for p in self._plans_list}
        for p in ctx.plans:
            if p.goal_name not in existing_names:
                self._plans_list.append(p)

        ctx_names = {p.goal_name for p in ctx.plans}
        new = tuple(p for p in self._plans_list if p.goal_name not in ctx_names)
        if not new:
            return ctx
        return replace(ctx, plans=(*ctx.plans, *new))


# =============================================================================
# WorldModel — resource/environment tracking
# =============================================================================


@dataclass(frozen=True)
class WorldModel:
    """Tracks resources and environment state.

    compile_life: provides track_resource, list_resources, remove_resource tools.
    compile_cognitive: merges tracked resources into CognitiveContext.resources.

    Resources: money, accounts, compute, projects, anything the agent reasons about.
    """

    _resources: tuple[ResourceSpec, ...] = ()
    _resources_list: list[ResourceSpec] = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        self._resources_list.extend(self._resources)

    def compile_life(self, ctx: LifeContext) -> LifeContext:
        resources_list = self._resources_list
        tick = ctx.tick

        @tool("Track a resource (money, account, compute, project, etc.)")
        def track_resource(
            name: str, resource_type: str, value: str,
        ) -> dict[str, str | bool]:
            # Update existing or add new
            for i, r in enumerate(resources_list):
                if r.name == name:
                    resources_list[i] = replace(r, value=value, last_updated_tick=tick)
                    return {"tracked": True, "name": name, "updated": True}
            resources_list.append(ResourceSpec(
                name=name, resource_type=resource_type,
                value=value, last_updated_tick=tick,
            ))
            return {"tracked": True, "name": name}

        @tool("List all tracked resources.")
        def list_resources() -> dict[str, list[dict[str, str | int]]]:
            return {
                "resources": [
                    {
                        "name": r.name,
                        "type": r.resource_type,
                        "value": r.value,
                        "last_updated_tick": r.last_updated_tick,
                    }
                    for r in resources_list
                ],
            }

        @tool("Stop tracking a resource.")
        def remove_resource(name: str) -> dict[str, str | bool]:
            for i, r in enumerate(resources_list):
                if r.name == name:
                    resources_list.pop(i)
                    return {"removed": True, "name": name}
            return {"error": f"resource '{name}' not found"}

        msgs = ctx.messages
        if resources_list:
            parts = [f"  - {r.name} ({r.resource_type}): {r.value}" for r in resources_list]
            msgs = (*msgs, system(text="WORLD MODEL — tracked resources:\n" + "\n".join(parts)))

        return replace(
            ctx,
            messages=msgs,
            tools=(*ctx.tools, track_resource, list_resources, remove_resource),
        )

    def compile_cognitive(self, ctx: CognitiveContext) -> CognitiveContext:
        # Seed from persisted resources
        existing_names = {r.name for r in self._resources_list}
        for r in ctx.resources:
            if r.name not in existing_names:
                self._resources_list.append(r)

        ctx_names = {r.name for r in ctx.resources}
        new = tuple(r for r in self._resources_list if r.name not in ctx_names)
        if not new:
            return ctx
        return replace(ctx, resources=(*ctx.resources, *new))


# =============================================================================
# TaskQueue — incoming + self-generated task management
# =============================================================================


@dataclass(frozen=True)
class TaskQueue:
    """Manages incoming + self-generated tasks.

    compile_life: provides add_task, claim_task, complete_task, list_tasks tools.
    compile_cognitive: merges managed tasks into CognitiveContext.tasks.

    Tasks feed plans. Author tasks = external input. Self tasks = from planning.
    """

    _tasks: tuple[TaskSpec, ...] = ()
    _tasks_list: list[TaskSpec] = field(default_factory=list, repr=False)
    _next_id: list[int] = field(default_factory=lambda: [1], repr=False)

    def __post_init__(self) -> None:
        self._tasks_list.extend(self._tasks)
        # Set next_id beyond existing IDs
        if self._tasks:
            max_id = max(int(t.id) for t in self._tasks if t.id.isdigit())
            self._next_id[0] = max_id + 1

    def compile_life(self, ctx: LifeContext) -> LifeContext:
        tasks_list = self._tasks_list
        next_id = self._next_id
        tick = ctx.tick

        @tool("Add a new task to the queue. Self-generated or from planning.")
        def add_task(
            description: str, priority: int = 1,
            plan_name: str = "", deadline_tick: int = 0,
        ) -> dict[str, str | bool]:
            task_id = str(next_id[0])
            next_id[0] += 1
            tasks_list.append(TaskSpec(
                id=task_id, description=description, source="self",
                priority=priority, status="pending",
                deadline_tick=deadline_tick, plan_name=plan_name,
                created_tick=tick,
            ))
            return {"added": True, "id": task_id}

        @tool("Claim a task — mark it as in_progress.")
        def claim_task(task_id: str) -> dict[str, str | bool]:
            for i, t in enumerate(tasks_list):
                if t.id == task_id:
                    tasks_list[i] = replace(t, status="in_progress")
                    return {"claimed": True, "task_id": task_id}
            return {"error": f"task '{task_id}' not found"}

        @tool("Complete a task with a result.")
        def complete_task(task_id: str, result: str = "") -> dict[str, str | bool]:
            for i, t in enumerate(tasks_list):
                if t.id == task_id:
                    tasks_list[i] = replace(t, status="completed")
                    return {"completed": True, "task_id": task_id}
            return {"error": f"task '{task_id}' not found"}

        @tool("List all tasks with their statuses.")
        def list_tasks() -> dict[str, list[dict[str, str | int]]]:
            return {
                "tasks": [
                    {
                        "id": t.id,
                        "description": t.description,
                        "source": t.source,
                        "priority": t.priority,
                        "status": t.status,
                        "plan_name": t.plan_name,
                    }
                    for t in tasks_list
                ],
            }

        msgs = ctx.messages
        pending = [t for t in tasks_list if t.status == "pending"]
        if pending:
            pending.sort(key=lambda t: t.priority)
            parts = [f"  - [{t.source}] #{t.id}: {t.description} (priority={t.priority})" for t in pending[:10]]
            msgs = (*msgs, system(text=f"TASK QUEUE ({len(pending)} pending):\n" + "\n".join(parts)))

        return replace(
            ctx,
            messages=msgs,
            tools=(*ctx.tools, add_task, claim_task, complete_task, list_tasks),
        )

    def compile_cognitive(self, ctx: CognitiveContext) -> CognitiveContext:
        # Seed from persisted tasks
        existing_ids = {t.id for t in self._tasks_list}
        for t in ctx.tasks:
            if t.id not in existing_ids:
                self._tasks_list.append(t)

        ctx_ids = {t.id for t in ctx.tasks}
        new = tuple(t for t in self._tasks_list if t.id not in ctx_ids)
        if not new:
            return ctx
        return replace(ctx, tasks=(*ctx.tasks, *new))

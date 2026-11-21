from __future__ import annotations

import re
from collections import defaultdict, deque
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.workflows.expression import validate_expression

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def _validate_cron_field(token: str, min_val: int, max_val: int) -> None:
    if not token:
        raise ValueError("Empty cron field")
    for part in token.split(","):
        if "/" in part:
            base, step_str = part.split("/", 1)
            if not step_str.isdigit() or int(step_str) <= 0:
                raise ValueError(f"Invalid cron step: {part}")
        else:
            base = part
        if base == "*":
            continue
        if "-" in base:
            lo_s, hi_s = base.split("-", 1)
            if not (lo_s.isdigit() and hi_s.isdigit()):
                raise ValueError(f"Invalid cron range: {part}")
            lo, hi = int(lo_s), int(hi_s)
            if lo < min_val or hi > max_val or lo > hi:
                raise ValueError(f"Cron range out of bounds: {part}")
        elif base.isdigit():
            val = int(base)
            if val < min_val or val > max_val:
                raise ValueError(f"Cron value out of bounds: {part}")
        else:
            raise ValueError(f"Invalid cron token: {part}")


def validate_cron_expression(expr: str) -> str:
    """Validate a standard 5-field cron expression (minute hour dom month dow)."""
    parts = expr.strip().split()
    if len(parts) != 5:
        raise ValueError("Cron expression must have exactly 5 whitespace-separated fields.")
    bounds = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 7)]
    for field_val, (lo, hi) in zip(parts, bounds, strict=True):
        _validate_cron_field(field_val, lo, hi)
    return " ".join(parts)


class ScheduleTrigger(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["schedule"] = "schedule"
    mode: Literal["daily", "weekly", "interval", "cron"]
    time: str | None = None
    weekdays: list[int] | None = None
    every_minutes: int | None = Field(default=None, ge=5, le=10080)
    cron: str | None = None

    @field_validator("time")
    @classmethod
    def _check_time(cls, v: str | None) -> str | None:
        if v is not None and not _TIME_RE.match(v):
            raise ValueError("time must be in 24-hour HH:MM format (00:00 to 23:59).")
        return v

    @field_validator("weekdays")
    @classmethod
    def _check_weekdays(cls, v: list[int] | None) -> list[int] | None:
        if v is not None:
            if not v:
                raise ValueError("weekdays cannot be empty when provided.")
            for day in v:
                if day < 0 or day > 6:
                    raise ValueError("weekdays must contain integers from 0 (Mon) to 6 (Sun).")
        return v

    @model_validator(mode="after")
    def _check_mode_requirements(self) -> ScheduleTrigger:
        if self.mode == "daily":
            if not self.time:
                raise ValueError("daily schedule requires time (HH:MM).")
        elif self.mode == "weekly":
            if not self.time or not self.weekdays:
                raise ValueError("weekly schedule requires both time (HH:MM) and weekdays.")
        elif self.mode == "interval":
            if self.every_minutes is None:
                raise ValueError("interval schedule requires every_minutes (5 to 10080).")
        elif self.mode == "cron":
            if not self.cron or not self.cron.strip():
                raise ValueError("cron schedule requires a 5-field cron expression.")
            self.cron = validate_cron_expression(self.cron)
        return self


class EventTrigger(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["event"] = "event"
    source: str = Field(min_length=1, max_length=120)


class ManualTrigger(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["manual"] = "manual"


TriggerDefinition = Annotated[
    ScheduleTrigger | EventTrigger | ManualTrigger,
    Field(discriminator="type"),
]


class _BaseNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=64)
    label: str | None = Field(default=None, max_length=200)


class ClassifyNode(_BaseNode):
    type: Literal["classify"] = "classify"
    prompt: str | None = None
    labels: list[str] | None = None
    confidence_threshold: float = Field(default=0.0, ge=0.0, le=1.0)
    provider: Literal["auto", "groq", "gemini"] = "auto"
    model: str | None = None


class RetrieveNode(_BaseNode):
    type: Literal["retrieve"] = "retrieve"
    query_template: str = Field(default="{{trigger.body}}", min_length=1, max_length=1000)
    top_k: int = Field(default=5, ge=1, le=20)
    include_company_shared: bool = True
    mode: Literal["knowledge", "aggregate_ops"] = "knowledge"


class ExtractNode(_BaseNode):
    type: Literal["extract"] = "extract"
    prompt: str | None = None
    output_schema: dict[str, Any] | None = None
    confidence_threshold: float = Field(default=0.0, ge=0.0, le=1.0)
    provider: Literal["auto", "groq", "gemini"] = "auto"
    model: str | None = None


class AgentNode(_BaseNode):
    type: Literal["agent"] = "agent"
    prompt: str | None = None
    system_prompt: str | None = None
    require_sources: bool = False
    confidence_threshold: float = Field(default=0.0, ge=0.0, le=1.0)
    provider: Literal["auto", "groq", "gemini"] = "auto"
    model: str | None = None


class AINode(_BaseNode):
    """Internal/legacy AI config model used by DepartmentAiService; not part of WorkflowNode."""

    type: Literal["ai"] = "ai"
    task: Literal["classify", "summarize", "draft_reply", "extract", "score", "agent"]
    prompt: str | None = None
    labels: list[str] | None = None
    output_schema: dict[str, Any] | None = None
    confidence_threshold: float = Field(default=0.0, ge=0.0, le=1.0)
    provider: Literal["auto", "groq", "gemini"] = "auto"
    model: str | None = None


class ConditionNode(_BaseNode):
    type: Literal["condition"] = "condition"
    expression: str = Field(min_length=1)

    @field_validator("expression")
    @classmethod
    def _check_expression(cls, v: str) -> str:
        validate_expression(v)
        return v.strip()


class ApprovalNode(_BaseNode):
    type: Literal["approval"] = "approval"
    role: Literal["reviewer", "admin"] = "reviewer"
    timeout_hours: int | None = Field(default=None, ge=1, le=720)


class ActionNode(_BaseNode):
    type: Literal["action"] = "action"
    action: str = Field(min_length=1, max_length=120)
    params: dict[str, Any] = Field(default_factory=dict)


class WaitNode(_BaseNode):
    type: Literal["wait"] = "wait"
    wait_seconds: int = Field(default=60, ge=1, le=86400)
    until_expression: str | None = None


WorkflowNode = Annotated[
    ClassifyNode
    | RetrieveNode
    | ExtractNode
    | AgentNode
    | ConditionNode
    | ApprovalNode
    | ActionNode
    | WaitNode,
    Field(discriminator="type"),
]


class WorkflowEdge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str = Field(min_length=1, max_length=64)
    target: str = Field(min_length=1, max_length=64)
    label: Literal["true", "false"] | None = None


class WorkflowDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trigger: TriggerDefinition
    nodes: list[WorkflowNode] = Field(min_length=1, max_length=50)
    edges: list[WorkflowEdge] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _upgrade_legacy_ai_nodes(cls, data: Any) -> Any:
        """Backward-compatibility reader converting legacy 'ai' nodes into Classify/Extract/Agent."""
        if not isinstance(data, dict) or not isinstance(data.get("nodes"), list):
            return data
        upgraded_nodes: list[Any] = []
        changed = False
        for raw_node in data["nodes"]:
            if isinstance(raw_node, dict) and raw_node.get("type") == "ai":
                changed = True
                node = dict(raw_node)
                task = node.pop("task", None)
                if task == "classify":
                    node["type"] = "classify"
                    node.pop("output_schema", None)
                elif task == "extract":
                    node["type"] = "extract"
                    node.pop("labels", None)
                else:
                    node["type"] = "agent"
                    node.pop("labels", None)
                    node.pop("output_schema", None)
                upgraded_nodes.append(node)
            else:
                upgraded_nodes.append(raw_node)
        if not changed:
            return data
        return {**data, "nodes": upgraded_nodes}

    @model_validator(mode="after")
    def _validate_graph(self) -> WorkflowDefinition:
        node_ids: set[str] = set()
        nodes_by_id: dict[str, WorkflowNode] = {}
        for node in self.nodes:
            if node.id in node_ids:
                raise ValueError(f"Duplicate node id: '{node.id}'")
            node_ids.add(node.id)
            nodes_by_id[node.id] = node

        adj: dict[str, list[str]] = defaultdict(list)
        in_degree: dict[str, int] = {nid: 0 for nid in node_ids}
        outgoing_labels: dict[str, set[str | None]] = defaultdict(set)

        for edge in self.edges:
            if edge.source not in node_ids:
                raise ValueError(f"Edge references unknown source node: '{edge.source}'")
            if edge.target not in node_ids:
                raise ValueError(f"Edge references unknown target node: '{edge.target}'")
            if edge.source == edge.target:
                raise ValueError(f"Self-loop edge detected on node '{edge.source}'")
            adj[edge.source].append(edge.target)
            in_degree[edge.target] += 1
            outgoing_labels[edge.source].add(edge.label)

        queue: deque[str] = deque(nid for nid, deg in in_degree.items() if deg == 0)
        visited = 0
        while queue:
            curr = queue.popleft()
            visited += 1
            for nxt in adj[curr]:
                in_degree[nxt] -= 1
                if in_degree[nxt] == 0:
                    queue.append(nxt)

        if visited < len(node_ids):
            cycle_nodes = sorted(nid for nid, deg in in_degree.items() if deg > 0)
            raise ValueError(f"Cycle detected in workflow graph involving: {', '.join(cycle_nodes)}")

        for nid, node in nodes_by_id.items():
            if node.type == "condition":
                labels = outgoing_labels.get(nid, set())
                if "true" not in labels or "false" not in labels:
                    raise ValueError(
                        f"Condition node '{nid}' must have outgoing edges labeled 'true' and 'false'."
                    )

        return self

"""Schemas for Workflow DAG and Workflow / Version CRUD and publishing."""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class StepType(StrEnum):
    TRIGGER = "trigger"
    CLASSIFY = "classify"
    RETRIEVE = "retrieve"
    EXTRACT = "extract"
    AGENT = "agent"
    CONDITION = "condition"
    APPROVAL = "approval"
    ACTION = "action"
    WAIT = "wait"

class WorkflowVersionStatusEnum(StrEnum):
    DRAFT = "draft"
    TESTING = "testing"
    GATE_PASSED = "gate_passed"
    PUBLISHED = "published"
    PAUSED = "paused"
    FAILED = "failed"
    ARCHIVED = "archived"

class NodePosition(BaseModel):
    x: float = 0.0
    y: float = 0.0

class NodeDefinition(BaseModel):
    id: str
    type: StepType
    label: str
    config: dict[str, Any] = Field(default_factory=dict)
    position: NodePosition | None = None

class EdgeDefinition(BaseModel):
    id: str
    source: str
    target: str
    source_handle: str | None = None                                                 
    label: str | None = None

class WorkflowDAG(BaseModel):
    nodes: list[NodeDefinition] = Field(default_factory=list)
    edges: list[EdgeDefinition] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_graph(self) -> WorkflowDAG:
        if not self.nodes:
            return self

        node_ids = {n.id for n in self.nodes}
        if len(node_ids) != len(self.nodes):
            raise ValueError("Duplicate node IDs found in workflow DAG")

        for edge in self.edges:
            if edge.source not in node_ids:
                raise ValueError(f"Edge {edge.id} refers to non-existent source node: {edge.source}")
            if edge.target not in node_ids:
                raise ValueError(f"Edge {edge.id} refers to non-existent target node: {edge.target}")

        return self

class WorkflowCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    department_id: uuid.UUID | None = None
    initial_definition: WorkflowDAG | None = None

class WorkflowUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    department_id: uuid.UUID | None = None
    archived: bool | None = None

class WorkflowOut(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    department_id: uuid.UUID | None = None
    department_name: str | None = None
    name: str
    description: str | None = None
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None = None
    published_version_id: uuid.UUID | None = None
    latest_version_status: str = "draft"
    latest_version_number: int = 1

class WorkflowVersionCreate(BaseModel):
    definition: WorkflowDAG
    trigger_connection_id: uuid.UUID | None = None
    trigger_event_type: str | None = None
    change_note: str | None = None

class WorkflowVersionOut(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    workflow_id: uuid.UUID
    version_number: int
    definition: dict[str, Any]
    status: str
    trigger_connection_id: uuid.UUID | None = None
    trigger_event_type: str | None = None
    eval_score: Decimal | None = None
    change_note: str | None = None
    pause_reason: str | None = None
    created_at: datetime
    published_at: datetime | None = None

class DAGValidationResult(BaseModel):
    valid: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    execution_order: list[str] = Field(default_factory=list)

"""Phase 1 tests: WorkflowDefinition schema, safe condition expressions, and department timezone."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.department import DepartmentCreate, DepartmentUpdate
from app.workflows.definition import WorkflowDefinition
from app.workflows.expression import UnsafeExpressionError, evaluate_expression


def _base_ai_node(node_id: str = "ai_1") -> dict:
    return {
        "id": node_id,
        "type": "ai",
        "task": "classify",
        "labels": ["billing", "support"],
    }


def _base_action_node(node_id: str = "act_1") -> dict:
    return {
        "id": node_id,
        "type": "action",
        "action": "gmail.send_reply",
        "params": {"body": "{{ai_1.text}}"},
    }


def test_valid_definition_without_approval_node():
    defn = WorkflowDefinition.model_validate(
        {
            "trigger": {"type": "event", "source": "gmail.new_email"},
            "nodes": [_base_ai_node(), _base_action_node()],
            "edges": [{"source": "ai_1", "target": "act_1"}],
        }
    )
    assert defn.trigger.type == "event"
    assert len(defn.nodes) == 2


def test_valid_schedule_modes_and_manual_trigger():
    WorkflowDefinition.model_validate(
        {
            "trigger": {"type": "schedule", "mode": "daily", "time": "09:00"},
            "nodes": [_base_ai_node()],
        }
    )
    WorkflowDefinition.model_validate(
        {
            "trigger": {"type": "schedule", "mode": "weekly", "time": "17:30", "weekdays": [0, 4]},
            "nodes": [_base_ai_node()],
        }
    )
    WorkflowDefinition.model_validate(
        {
            "trigger": {"type": "schedule", "mode": "interval", "every_minutes": 15},
            "nodes": [_base_ai_node()],
        }
    )
    WorkflowDefinition.model_validate(
        {
            "trigger": {"type": "schedule", "mode": "cron", "cron": "*/15 9-17 * * 1-5"},
            "nodes": [_base_ai_node()],
        }
    )
    WorkflowDefinition.model_validate(
        {
            "trigger": {"type": "manual"},
            "nodes": [_base_ai_node()],
        }
    )


def test_daily_without_time_fails():
    with pytest.raises(ValidationError, match="daily schedule requires time"):
        WorkflowDefinition.model_validate(
            {
                "trigger": {"type": "schedule", "mode": "daily"},
                "nodes": [_base_ai_node()],
            }
        )


@pytest.mark.parametrize("bad_time", ["25:00", "9:00", "12:60", "abc", "24:00"])
def test_bad_time_fails(bad_time: str):
    with pytest.raises(ValidationError, match="HH:MM"):
        WorkflowDefinition.model_validate(
            {
                "trigger": {"type": "schedule", "mode": "daily", "time": bad_time},
                "nodes": [_base_ai_node()],
            }
        )


def test_weekly_rules():
    with pytest.raises(ValidationError, match="weekly schedule requires"):
        WorkflowDefinition.model_validate(
            {
                "trigger": {"type": "schedule", "mode": "weekly", "time": "09:00"},
                "nodes": [_base_ai_node()],
            }
        )
    with pytest.raises(ValidationError, match="weekdays"):
        WorkflowDefinition.model_validate(
            {
                "trigger": {"type": "schedule", "mode": "weekly", "time": "09:00", "weekdays": []},
                "nodes": [_base_ai_node()],
            }
        )
    with pytest.raises(ValidationError, match="0 \\(Mon\\) to 6 \\(Sun\\)"):
        WorkflowDefinition.model_validate(
            {
                "trigger": {"type": "schedule", "mode": "weekly", "time": "09:00", "weekdays": [7]},
                "nodes": [_base_ai_node()],
            }
        )


def test_interval_rules():
    with pytest.raises(ValidationError, match="interval schedule requires every_minutes"):
        WorkflowDefinition.model_validate(
            {
                "trigger": {"type": "schedule", "mode": "interval"},
                "nodes": [_base_ai_node()],
            }
        )
    with pytest.raises(ValidationError):
        WorkflowDefinition.model_validate(
            {
                "trigger": {"type": "schedule", "mode": "interval", "every_minutes": 4},
                "nodes": [_base_ai_node()],
            }
        )
    with pytest.raises(ValidationError):
        WorkflowDefinition.model_validate(
            {
                "trigger": {"type": "schedule", "mode": "interval", "every_minutes": 10081},
                "nodes": [_base_ai_node()],
            }
        )


@pytest.mark.parametrize("bad_cron", ["* * * *", "* * * * * *", "61 * * * *", "foo * * * *", ""])
def test_invalid_cron_fails(bad_cron: str):
    with pytest.raises(ValidationError):
        WorkflowDefinition.model_validate(
            {
                "trigger": {"type": "schedule", "mode": "cron", "cron": bad_cron},
                "nodes": [_base_ai_node()],
            }
        )


def test_duplicate_node_ids_fail():
    with pytest.raises(ValidationError, match="Duplicate node id"):
        WorkflowDefinition.model_validate(
            {
                "trigger": {"type": "manual"},
                "nodes": [_base_ai_node("dup"), _base_action_node("dup")],
            }
        )


def test_unknown_edge_target_fails():
    with pytest.raises(ValidationError, match="unknown target node"):
        WorkflowDefinition.model_validate(
            {
                "trigger": {"type": "manual"},
                "nodes": [_base_ai_node("ai_1")],
                "edges": [{"source": "ai_1", "target": "missing"}],
            }
        )


def test_cycle_detection_fails():
    with pytest.raises(ValidationError, match="Cycle detected"):
        WorkflowDefinition.model_validate(
            {
                "trigger": {"type": "manual"},
                "nodes": [_base_ai_node("n1"), _base_action_node("n2")],
                "edges": [
                    {"source": "n1", "target": "n2"},
                    {"source": "n2", "target": "n1"},
                ],
            }
        )


def test_condition_missing_true_or_false_branch_fails():
    with pytest.raises(ValidationError, match="outgoing edges labeled 'true' and 'false'"):
        WorkflowDefinition.model_validate(
            {
                "trigger": {"type": "manual"},
                "nodes": [
                    {"id": "cond_1", "type": "condition", "expression": "ai_1.score >= 80"},
                    _base_action_node("act_1"),
                ],
                "edges": [{"source": "cond_1", "target": "act_1", "label": "true"}],
            }
        )


def test_condition_with_both_branches_succeeds_and_evaluates_safely():
    WorkflowDefinition.model_validate(
        {
            "trigger": {"type": "manual"},
            "nodes": [
                {"id": "ai_1", "type": "ai", "task": "score"},
                {"id": "cond_1", "type": "condition", "expression": "ai_1.score >= 80"},
                _base_action_node("act_true"),
                _base_action_node("act_false"),
            ],
            "edges": [
                {"source": "ai_1", "target": "cond_1"},
                {"source": "cond_1", "target": "act_true", "label": "true"},
                {"source": "cond_1", "target": "act_false", "label": "false"},
            ],
        }
    )

    assert evaluate_expression("ai_1.score >= 80", {"ai_1": {"score": 90}}) is True
    assert evaluate_expression("ai_1.score >= 80", {"ai_1": {"score": 50}}) is False

    with pytest.raises(UnsafeExpressionError):
        evaluate_expression("__import__('os').system('ls')", {})


def test_department_timezone_validation():
    default_dept = DepartmentCreate(name="Support")
    assert default_dept.timezone == "Asia/Karachi"

    ny_dept = DepartmentCreate(name="Sales", timezone="America/New_York")
    assert ny_dept.timezone == "America/New_York"

    with pytest.raises(ValidationError, match="Invalid IANA timezone"):
        DepartmentCreate(name="Bad", timezone="Mars/Olympus_Mons")

    with pytest.raises(ValidationError, match="Invalid IANA timezone"):
        DepartmentUpdate(timezone="Not/A_Zone")


def test_missing_trigger_and_multiple_triggers_fail():
    with pytest.raises(ValidationError):
        WorkflowDefinition.model_validate(
            {
                "nodes": [_base_ai_node("n1"), _base_action_node("n2")],
                "edges": [{"source": "n1", "target": "n2"}],
            }
        )

    with pytest.raises(ValidationError):
        WorkflowDefinition.model_validate(
            {
                "trigger": {"type": "manual"},
                "triggers": [{"type": "manual"}, {"type": "event", "source": "gmail.new_email"}],
                "nodes": [_base_ai_node("n1")],
            }
        )

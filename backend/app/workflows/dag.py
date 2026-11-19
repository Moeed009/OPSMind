
from __future__ import annotations

from collections import defaultdict, deque

from app.schemas.workflow import DAGValidationResult, StepType, WorkflowDAG


class DAGValidationError(Exception):
    def __init__(self, message: str, errors: list[str] | None = None):
        super().__init__(message)
        self.errors = errors or [message]

def validate_dag(dag: WorkflowDAG) -> DAGValidationResult:
    """Performs static analysis on a WorkflowDAG:
    1. Single Trigger node requirement.
    2. Acyclicity test via Kahn's algorithm (cycle detection).
    3. Reachability from Trigger (isolated/disconnected node detection).
    4. Condition node branching check.
    """
    errors: list[str] = []
    warnings: list[str] = []
    execution_order: list[str] = []

    if not dag.nodes:
        return DAGValidationResult(valid=False, errors=["Workflow must contain at least one node."])

    nodes_by_id = {node.id: node for node in dag.nodes}
    triggers = [n for n in dag.nodes if n.type == StepType.TRIGGER]

    if len(triggers) == 0:
        errors.append("Workflow DAG must contain a Trigger node as the entry point.")
    elif len(triggers) > 1:
        errors.append(f"Workflow DAG has {len(triggers)} triggers. Only 1 trigger node is allowed.")

    
    adj: dict[str, list[str]] = defaultdict(list)
    in_degree: dict[str, int] = {node.id: 0 for node in dag.nodes}

    for edge in dag.edges:
        if edge.source not in nodes_by_id or edge.target not in nodes_by_id:
            continue
        adj[edge.source].append(edge.target)
        in_degree[edge.target] += 1

    
    queue: deque[str] = deque([nid for nid, deg in in_degree.items() if deg == 0])
    visited_count = 0

    while queue:
        curr = queue.popleft()
        execution_order.append(curr)
        visited_count += 1
        for neighbor in adj[curr]:
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    if visited_count < len(dag.nodes):
        unvisited = [nid for nid, deg in in_degree.items() if deg > 0]
        errors.append(f"Cycle detected in workflow graph involving nodes: {', '.join(unvisited)}")

    
    if len(triggers) == 1:
        trigger_id = triggers[0].id
        reachable: set[str] = set()
        dfs_queue = [trigger_id]
        while dfs_queue:
            curr = dfs_queue.pop()
            if curr not in reachable:
                reachable.add(curr)
                dfs_queue.extend(adj[curr])

        unreachable = [n.id for n in dag.nodes if n.id not in reachable]
        if unreachable:
            warnings.append(
                f"Nodes unreachable from trigger: {', '.join(unreachable)}. They will never execute."
            )

    
    for node in dag.nodes:
        if node.type == StepType.CONDITION:
            outgoing = [e for e in dag.edges if e.source == node.id]
            if len(outgoing) == 0:
                warnings.append(f"Condition node '{node.label}' has no outgoing branches.")
            elif len(outgoing) == 1:
                warnings.append(f"Condition node '{node.label}' only has 1 branch (consider true and false branches).")

    is_valid = len(errors) == 0
    return DAGValidationResult(
        valid=is_valid,
        errors=errors,
        warnings=warnings,
        execution_order=execution_order if is_valid else [],
    )

from __future__ import annotations

import ast
from typing import Any

_ALLOWED_AST_NODES = (
    ast.Expression,
    ast.BoolOp,
    ast.And,
    ast.Or,
    ast.UnaryOp,
    ast.Not,
    ast.Compare,
    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
    ast.In,
    ast.NotIn,
    ast.Name,
    ast.Load,
    ast.Attribute,
    ast.Subscript,
    ast.Constant,
    ast.List,
    ast.Tuple,
)


class UnsafeExpressionError(ValueError):
    """Raised when a condition expression uses disallowed syntax or operators."""


def validate_expression(expr: str) -> ast.Expression:
    """Parse and validate a condition expression against a strict AST whitelist."""
    if not expr or not expr.strip():
        raise UnsafeExpressionError("Condition expression must be non-empty.")
    try:
        tree = ast.parse(expr.strip(), mode="eval")
    except SyntaxError as exc:
        raise UnsafeExpressionError(f"Invalid condition syntax: {exc.msg}") from exc

    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_AST_NODES):
            raise UnsafeExpressionError(
                f"Disallowed expression construct: {type(node).__name__}"
            )
        if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            raise UnsafeExpressionError("Private or dunder attribute access is not allowed.")
        if isinstance(node, ast.Name) and node.id.startswith("_"):
            raise UnsafeExpressionError("Private or dunder identifier access is not allowed.")

    return tree


def _eval_node(node: ast.AST, context: dict[str, Any]) -> Any:
    if isinstance(node, ast.Expression):
        return _eval_node(node.body, context)
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.List):
        return [_eval_node(elt, context) for elt in node.elts]
    if isinstance(node, ast.Tuple):
        return tuple(_eval_node(elt, context) for elt in node.elts)
    if isinstance(node, ast.Name):
        if node.id in ("True", "true"):
            return True
        if node.id in ("False", "false"):
            return False
        if node.id in ("None", "null"):
            return None
        return context.get(node.id)
    if isinstance(node, ast.Attribute):
        base = _eval_node(node.value, context)
        if isinstance(base, dict):
            return base.get(node.attr)
        return getattr(base, node.attr, None)
    if isinstance(node, ast.Subscript):
        base = _eval_node(node.value, context)
        key = _eval_node(node.slice, context)
        if isinstance(base, dict):
            return base.get(key)
        if isinstance(base, (list, tuple)) and isinstance(key, int):
            return base[key] if -len(base) <= key < len(base) else None
        return None
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return not bool(_eval_node(node.operand, context))
    if isinstance(node, ast.BoolOp):
        if isinstance(node.op, ast.And):
            return all(bool(_eval_node(v, context)) for v in node.values)
        if isinstance(node.op, ast.Or):
            return any(bool(_eval_node(v, context)) for v in node.values)
    if isinstance(node, ast.Compare):
        left = _eval_node(node.left, context)
        for op, comparator in zip(node.ops, node.comparators, strict=True):
            right = _eval_node(comparator, context)
            if isinstance(op, ast.Eq):
                ok = left == right
            elif isinstance(op, ast.NotEq):
                ok = left != right
            elif isinstance(op, ast.Lt):
                ok = left is not None and right is not None and left < right
            elif isinstance(op, ast.LtE):
                ok = left is not None and right is not None and left <= right
            elif isinstance(op, ast.Gt):
                ok = left is not None and right is not None and left > right
            elif isinstance(op, ast.GtE):
                ok = left is not None and right is not None and left >= right
            elif isinstance(op, ast.In):
                ok = right is not None and left in right
            elif isinstance(op, ast.NotIn):
                ok = right is None or left not in right
            else:
                raise UnsafeExpressionError(f"Unsupported operator: {type(op).__name__}")
            if not ok:
                return False
            left = right
        return True
    raise UnsafeExpressionError(f"Unsupported AST node: {type(node).__name__}")


def evaluate_expression(expr: str, context: dict[str, Any]) -> bool:
    """Safely evaluate a validated condition expression against step outputs."""
    tree = validate_expression(expr)
    return bool(_eval_node(tree, context))

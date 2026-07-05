"""
executors/python_math.py
Safe arithmetic evaluator using Python's AST — no eval(), no exec().
Only allows number literals and the operators +, -, *, /, //, **, %.
Any other node type raises ValueError immediately.

config keys:
  (none — all input comes from args)

args (defined in YAML):
  expression  string  Required  The arithmetic expression, e.g. "2 ** 10 + 5"
"""
from __future__ import annotations

import ast
import operator
from typing import Any

from executors.base import AbstractExecutor

_SAFE_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}

_MAX_EXPRESSION_LEN = 256
_MAX_POWER_BASE = 1_000_000
_MAX_POWER_EXP = 100


def _safe_eval(node: ast.AST) -> float:
    if isinstance(node, ast.Expression):
        return _safe_eval(node.body)
    if isinstance(node, ast.Constant):
        if not isinstance(node.value, (int, float)):
            raise ValueError(f"Only numeric literals allowed, got {type(node.value).__name__}")
        return float(node.value)
    if isinstance(node, ast.BinOp):
        op_cls = type(node.op)
        if op_cls not in _SAFE_OPERATORS:
            raise ValueError(f"Operator {op_cls.__name__} is not allowed")
        left = _safe_eval(node.left)
        right = _safe_eval(node.right)
        # Prevent DoS via massive exponentiation: 9999**9999 etc.
        if isinstance(node.op, ast.Pow) and (abs(left) > _MAX_POWER_BASE or abs(right) > _MAX_POWER_EXP):
            raise ValueError(
                f"Exponentiation operands too large (max base {_MAX_POWER_BASE}, max exp {_MAX_POWER_EXP})"
            )
        return _SAFE_OPERATORS[op_cls](left, right)
    if isinstance(node, ast.UnaryOp):
        op_cls = type(node.op)
        if op_cls not in _SAFE_OPERATORS:
            raise ValueError(f"Unary operator {op_cls.__name__} is not allowed")
        return _SAFE_OPERATORS[op_cls](_safe_eval(node.operand))
    raise ValueError(f"Expression contains unsupported node: {type(node).__name__}")


class PythonMathExecutor(AbstractExecutor):
    async def execute(self, args: dict[str, Any]) -> Any:
        expression: str = str(args.get("expression", "")).strip()
        if not expression:
            raise ValueError("arg 'expression' is required")
        if len(expression) > _MAX_EXPRESSION_LEN:
            raise ValueError(f"Expression too long (max {_MAX_EXPRESSION_LEN} chars)")

        try:
            tree = ast.parse(expression, mode="eval")
        except SyntaxError as e:
            raise ValueError(f"Invalid expression syntax: {e}") from e

        result = _safe_eval(tree)
        # Return int if the result is a whole number
        if result == int(result) and abs(result) < 2**53:
            return {"expression": expression, "result": int(result)}
        return {"expression": expression, "result": result}

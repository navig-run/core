import ast
import operator
from typing import Any

# Supported operators
calc_operators = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
    ast.Not: operator.not_,
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.Is: operator.is_,
    ast.IsNot: operator.is_not,
    ast.In: lambda x, y: x in y,
    ast.NotIn: lambda x, y: x not in y,
    ast.And: lambda x, y: x and y,
    ast.Or: lambda x, y: x or y,
}


def safe_eval(expr: str, variables: dict[str, Any] | None = None) -> Any:
    """
    Safely evaluate a simple Python expression.

    Supports:
    - Arithmetic (+, -, *, /, //, %, **)
    - Comparison (==, !=, <, <=, >, >=, is, in)
    - Logic (and, or, not)
    - Variables via 'variables' dict
    - Basic types (strings, numbers, booleans, lists, dicts, tuples)
    - NO function calls (calls are blocked)
    - NO attribute access (obj.prop is blocked to prevent method chaining)
    """
    if variables is None:
        variables = {}

    try:
        node = ast.parse(expr, mode="eval")
        return _eval_node(node.body, variables)
    except Exception as e:
        raise ValueError(f"Evaluation failed: {e}") from e


def _eval_node(node: ast.AST, variables: dict[str, Any]) -> Any:
    # Literals
    if isinstance(node, ast.Constant):  # Python 3.8+
        return node.value
    if isinstance(
        node, (ast.Str, ast.Num, ast.Bytes, ast.NameConstant)
    ):  # Python < 3.8
        return (
            node.n
            if isinstance(node, ast.Num)
            else node.s if isinstance(node, ast.Str) else node.value
        )

    # Data structures
    if isinstance(node, ast.List):
        return [_eval_node(x, variables) for x in node.elts]
    if isinstance(node, ast.Tuple):
        return tuple([_eval_node(x, variables) for x in node.elts])
    if isinstance(node, ast.Dict):
        return {
            _eval_node(k, variables): _eval_node(v, variables)
            for k, v in zip(node.keys, node.values)
        }

    # Variables
    if isinstance(node, ast.Name):
        if node.id in variables:
            return variables[node.id]
        if node.id == "True":
            return True
        if node.id == "False":
            return False
        if node.id == "None":
            return None
        raise ValueError(f"Unknown variable: {node.id}")

    # Unary Ops (not, -x)
    if isinstance(node, ast.UnaryOp):
        op = calc_operators.get(type(node.op))
        if op:
            return op(_eval_node(node.operand, variables))
        raise ValueError(f"Unknown unary operator: {type(node.op)}")

    # Binary Ops (x + y, x == y)
    if isinstance(node, ast.BinOp):
        op = calc_operators.get(type(node.op))
        if op:
            return op(
                _eval_node(node.left, variables), _eval_node(node.right, variables)
            )
        raise ValueError(f"Unknown binary operator: {type(node.op)}")

    if isinstance(node, ast.Compare):
        left = _eval_node(node.left, variables)
        for op, comparator in zip(node.ops, node.comparators):
            op_func = calc_operators.get(type(op))
            if not op_func:
                raise ValueError(f"Unknown comparison operator: {type(op)}")
            right = _eval_node(comparator, variables)
            if not op_func(left, right):
                return False
            left = right
        return True

    # Boolean Ops (and, or)
    if isinstance(node, ast.BoolOp):
        values = [_eval_node(v, variables) for v in node.values]
        if isinstance(node.op, ast.And):
            return all(values)
        if isinstance(node.op, ast.Or):
            return any(values)
        raise ValueError(f"Unknown boolean operator: {type(node.op)}")

    # Subscript (x[y]) - Allowed for lists/dicts
    if isinstance(node, ast.Subscript):
        val = _eval_node(node.value, variables)
        idx = _eval_node(node.slice, variables)
        return val[idx]

    raise ValueError(f"Unsupported expression node: {type(node)}")

"""The arithmetic of derived measures as ClickHouse SQL: operands as Float64 columns, every division guarded
against zero, so ``revenue / ad_spend`` is NULL (no ratio) where nothing was spent instead of an error."""

from __future__ import annotations

import re

from omw.metrics.schema import Catalog


def guard_divisions(expression: str) -> str:
    """Wrap every divisor (a name or a parenthesised group) in ``nullIf(..., 0)``."""
    out = []
    i = 0
    while i < len(expression):
        char = expression[i]
        if char != "/":
            out.append(char)
            i += 1
            continue
        j = i + 1
        while j < len(expression) and expression[j] == " ":
            j += 1
        if j < len(expression) and expression[j] == "(":
            depth, k = 0, j
            while k < len(expression):
                depth += expression[k] == "("
                depth -= expression[k] == ")"
                k += 1
                if depth == 0:
                    break
        else:
            k = j
            while k < len(expression) and (expression[k].isalnum() or expression[k] in "_."):
                k += 1
            if k < len(expression) and expression[k] == "(":  # a function call: include its arguments
                depth = 0
                while k < len(expression):
                    depth += expression[k] == "("
                    depth -= expression[k] == ")"
                    k += 1
                    if depth == 0:
                        break
        out.append(f"/ nullIf({expression[j:k]}, 0)")
        i = k
    return "".join(out)


def derived_sql(catalog: Catalog, name: str) -> str:
    """The derived expression over Float64 column references, every division guarded against zero."""
    derived = catalog.derived[name]
    expression = derived.derived
    for operand in derived.operands:
        if operand in catalog.derived:
            replacement = f"({derived_sql(catalog, operand)})"
        else:
            replacement = f"toFloat64({operand})"
        expression = re.sub(rf"(?<![a-z0-9_]){operand}(?![a-z0-9_])", replacement, expression)
    return guard_divisions(expression)


def output_select(catalog: Catalog, requested: list[str]) -> list[str]:
    """Output columns in the requested order: a base metric by name, a derived one by its expression."""
    return [
        name if name in catalog.metrics else f"{derived_sql(catalog, name)} AS {name}" for name in requested
    ]

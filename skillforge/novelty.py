"""Measured (not asserted) novelty of synthesised programs.

* AST structural similarity: multiset Jaccard over node-type paths of length 3 (identifiers, constants and
  formatting are erased, so renaming or re-commenting code does not look new).
* Graph signature: the execution-graph topology (node kinds, branch nesting, LLM vs code).
* Behavioural distance: mean absolute difference of per-instance scores on the same instances.

A candidate is counted as a *new algorithm* only if its AST similarity to every earlier candidate in the
same run is below NOVEL_AST_THRESHOLD and it is executable. A mutation counts as *structural* only if the
graph signature changed, or the set of functions changed, or AST similarity to the parent is below
STRUCTURAL_AST_THRESHOLD; otherwise it is *cosmetic/parametric*.
"""

from __future__ import annotations

import ast
from collections import Counter
from typing import Any

NOVEL_AST_THRESHOLD = 0.60
STRUCTURAL_AST_THRESHOLD = 0.90


def _paths(node: ast.AST, prefix: tuple[str, ...], out: Counter) -> None:
    name = type(node).__name__
    path = (prefix + (name,))[-3:]
    if len(path) == 3:
        out[path] += 1
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.Load, ast.Store, ast.Del)):
            continue
        _paths(child, path, out)


def ast_profile(source: str) -> Counter:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return Counter()
    c: Counter = Counter()
    _paths(tree, (), c)
    return c


def ast_similarity(a: str | Counter, b: str | Counter) -> float:
    pa = a if isinstance(a, Counter) else ast_profile(a)
    pb = b if isinstance(b, Counter) else ast_profile(b)
    if not pa and not pb:
        return 1.0
    inter = sum((pa & pb).values())
    union = sum((pa | pb).values())
    return inter / union if union else 1.0


def function_names(source: str) -> set[str]:
    try:
        return {n.name for n in ast.parse(source).body if isinstance(n, ast.FunctionDef)}
    except SyntaxError:
        return set()


def graph_signature(pipeline: list[dict[str, Any]]) -> str:
    def rec(nodes):
        parts = []
        for n in nodes:
            k = n.get("kind")
            if k == "code":
                parts.append("C")
            elif k == "llm":
                parts.append("L")
            elif k == "branch":
                parts.append("B(" + rec(n.get("then", [])) + "|" + rec(n.get("otherwise", [])) + ")")
            else:
                parts.append("?")
        return "".join(parts)
    return rec(pipeline)


def behavioural_distance(a: list[float], b: list[float]) -> float:
    if not a or len(a) != len(b):
        return 1.0
    return sum(abs(x - y) for x, y in zip(a, b)) / len(a)


def classify_change(parent_src: str, parent_pipe: list, child_src: str, child_pipe: list) -> dict[str, Any]:
    sim = ast_similarity(parent_src, child_src)
    g_changed = graph_signature(parent_pipe) != graph_signature(child_pipe)
    f_changed = function_names(parent_src) != function_names(child_src)
    structural = g_changed or f_changed or sim < STRUCTURAL_AST_THRESHOLD
    return {"ast_similarity_to_parent": round(sim, 4), "graph_changed": g_changed, "functions_changed": f_changed,
            "structural": structural,
            "added_functions": sorted(function_names(child_src) - function_names(parent_src)),
            "removed_functions": sorted(function_names(parent_src) - function_names(child_src))}

"""Security invariants: no shell=True sites in source."""

from __future__ import annotations

import ast
from pathlib import Path


def test_no_shell_true_in_src() -> None:
    """Walk the AST rather than greping text so docstrings/comments don't trip us."""
    src = Path(__file__).resolve().parent.parent / "src"
    hits: list[tuple[Path, int]] = []
    for py in src.rglob("*.py"):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.keyword) and node.arg == "shell":
                val = node.value
                if isinstance(val, ast.Constant) and val.value is True:
                    hits.append((py, node.lineno))
    assert hits == [], f"found shell=True call site(s): {hits}"

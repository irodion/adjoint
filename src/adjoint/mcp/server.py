"""MCP stdio entrypoint.

M0 scaffold: prints a helpful message to stderr and exits non-zero so Claude
Code reports the missing implementation clearly. M3 replaces this with a
FastMCP server exposing tools/resources.
"""

from __future__ import annotations

import sys


def main() -> int:
    sys.stderr.write(
        "adjoint-mcp: MCP server is not yet implemented (lands in M3).\n"
        "Install with `pip install adjoint[mcp]` once available.\n"
    )
    return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

"""Claude Code hook entrypoints — installed as pinned ``adjoint-hook-*`` binaries.

Each hook is a tiny Python module whose ``main()`` is an installed console
script. They share ``_runtime.py`` for stdin parsing, the recursion guard,
daemon detection, and per-hook timeout enforcement.
"""

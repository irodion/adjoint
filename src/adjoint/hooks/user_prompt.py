"""UserPromptSubmit hook — inject [[wikilinks]] into user prompts (opt-in).

Default off per config (``memory.enrich_prompts = false``). M2 enables.
"""

from __future__ import annotations

import sys
from typing import Any

from ._runtime import HookInput, run_hook


def handle(hook_input: HookInput) -> dict[str, Any] | None:
    # TODO(M2): if config.memory.enrich_prompts, grep concepts/ and inject links.
    return None


def main() -> int:
    return run_hook("user_prompt", handle, timeout_s=1.0, fail_open=True)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

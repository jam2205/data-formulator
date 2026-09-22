# Copyright (c) the quant-bridge project. Not part of upstream Microsoft
# data-formulator; kept in the fork near upstream, so this file lives with our
# other quant-bridge-specific additions rather than touching shared core code.

"""quant-bridge-analysis skill -- quant-bridge's read-only analytical tools,
exposed to the analyst agent as ordinary inspection tools.

Every call here is a thin pass-through to quant-bridge's REST tool proxy
(``POST /api/tool/<name>``) -- the SAME dispatch the research-harness UI and
the Claude Code chat in VSCodium use (see ``quant-bridge/src/rest.ts``: "one
definition of every tool; a second one would drift"). Nothing here
re-implements a calculation, and the tool *schemas* (``tools.json``) are
generated from quant-bridge's own ``/api/tools`` catalog by
``generate_analyst_tools.py`` rather than hand-copied, for the same reason.

Restricted to the non-mutating half of the catalog by construction (the
mutating tools -- ``annotate_chart`` / ``chainlog_append`` /
``registry_record`` / ``export_arrow_ipc`` -- are simply not in
``QB_ANALYSIS_TOOLS`` / ``tools.json``; quant-bridge's own REST layer would
403 them anyway without ``QB_ALLOW_WRITES=1``). See ``SKILL.md`` for the
discipline this implies: what these tools return is a candidate observation
for this session, never a finding reported on its own -- that's
``fx-research/newjob.py``'s job.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Generator

import requests

from data_formulator.analyst.skills.base import Event, SkillContext, ToolResult

logger = logging.getLogger(__name__)

BASE_URL = os.environ.get("QUANT_BRIDGE_BASE_URL", "http://localhost:3100").rstrip("/")
TIMEOUT_SECONDS = float(os.environ.get("QUANT_BRIDGE_TOOL_TIMEOUT_SECONDS", "60"))

# Model-facing tool name -> real quant-bridge MCP tool name. Kept in sync with
# QB_ANALYSIS_TOOLS in generate_analyst_tools.py (quant-bridge/clients/
# data-formulator/) -- that script is the source of truth for tools.json;
# this map only needs to strip the "qb_" prefix back off.
_PREFIX = "qb_"

# A dashboard/forecast result is small; leave headroom for query_sql, which
# can return real row sets.
_MAX_RESULT_CHARS = 16_000


class QuantBridgeAnalysisSkill:
    """The quant-bridge-analysis tool handler -- inspection tools only, no
    committing actions (``actions: []`` in SKILL.md)."""

    def handle_tool(self, name: str, args: dict[str, Any], ctx: SkillContext) -> ToolResult:
        if not name.startswith(_PREFIX):
            return ToolResult(text=f"quant-bridge-analysis has no tool '{name}'.")
        real_name = name[len(_PREFIX):]

        try:
            r = requests.post(
                f"{BASE_URL}/api/tool/{real_name}",
                json=args or {},
                timeout=TIMEOUT_SECONDS,
            )
        except requests.RequestException as e:
            return ToolResult(
                text=(
                    f"quant-bridge unreachable at {BASE_URL}: {e}. "
                    "The 'bridge: serve :3100' task is probably not running -- "
                    "say so rather than substituting a stale workspace table."
                )
            )

        # Mirrors quant_bridge_data_loader.py's own handling: the bridge answers
        # a bad call with a 400 whose BODY says what to fix, so read the JSON
        # body before deciding this failed rather than raising on status alone.
        try:
            body = r.json()
        except ValueError:
            return ToolResult(
                text=f"quant-bridge '{real_name}': non-JSON response (HTTP {r.status_code}): {r.text[:300]}"
            )

        if r.status_code >= 400 or body.get("ok") is False:
            detail = body.get("error") or body.get("data") or r.text[:300] or r.reason
            return ToolResult(text=f"quant-bridge '{real_name}' failed: {detail}")

        text = json.dumps(body.get("data"), indent=2, default=str)
        if len(text) > _MAX_RESULT_CHARS:
            omitted = len(text) - _MAX_RESULT_CHARS
            text = text[:_MAX_RESULT_CHARS] + f"\n... [truncated, {omitted} more characters -- narrow the query]"
        return ToolResult(text=text)

    def handle_action(
        self, action: str, spec: dict[str, Any], ctx: SkillContext,
    ) -> Generator[Event, None, str | None]:
        # No committing actions declared (SKILL.md `actions: []`); the agent
        # shell should never dispatch here, but fail loudly rather than
        # silently if it ever does.
        msg = f"quant-bridge-analysis has no action '{action}'."
        yield {"type": "error", "message": msg, "message_code": "agent.unknownAction"}
        return msg


def get_skill() -> QuantBridgeAnalysisSkill:
    """Factory used by the registry's eager instantiation."""
    return QuantBridgeAnalysisSkill()

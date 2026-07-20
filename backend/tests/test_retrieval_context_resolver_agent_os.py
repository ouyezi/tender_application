import json

import pytest

from app.services.retrieval.context_resolver_agent_os import AgentOSContextResolver


@pytest.mark.asyncio
async def test_agent_os_context_resolver_parses_actions():
    captured = {}

    async def fake_invoke(app_name, payload):
        captured["app"] = app_name
        return {
            "actions_json": json.dumps(["add_parent_intro", "add_siblings"]),
            "sibling_chunk_ids_json": json.dumps(["chk_auth"]),
        }

    resolver = AgentOSContextResolver(invoke_app=fake_invoke)
    out = await resolver.resolve_group(
        {
            "requirement": "独立法人",
            "query": "授权",
            "hits": [],
            "parent": {"chunk_id": "lg_q"},
            "siblings": [{"chunk_id": "chk_auth"}],
            "candidates": ["add_parent_intro", "add_siblings"],
        },
        ["add_parent_intro", "add_siblings"],
    )
    assert out["actions"] == ["add_parent_intro", "add_siblings"]
    assert out["sibling_chunk_ids"] == ["chk_auth"]
    assert captured["app"] == "retrieval_context_resolver_app"

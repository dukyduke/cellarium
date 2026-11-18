"""MCP: in-process tool calls + a real Streamable HTTP round trip with bearer auth."""

import asyncio
import json
import socket
import threading
import time

import pytest
import uvicorn
from mcp import Client

from cellarium import auth, cellar
from cellarium.app import create_app
from cellarium.mcp_server import build_mcp


def call(mcp, name, args=None):
    async def run():
        async with Client(mcp) as client:
            return await client.call_tool(name, args or {})
    return asyncio.run(run())


def payload(result):
    if result.structured_content is not None:
        data = result.structured_content
        return data.get("result", data) if isinstance(data, dict) and set(data) == {"result"} else data
    return json.loads(result.content[0].text)


OWNER = auth.Principal(1, "admin", "owner", token_name="test")


@pytest.fixture
def mcp(settings, conn):
    return build_mcp(settings, local_principal=OWNER)


def test_tools_listed(mcp):
    async def run():
        async with Client(mcp) as client:
            tools = await client.list_tools()
            prompts = await client.list_prompts()
            resources = await client.list_resources()
            return [t.name for t in tools.tools], [p.name for p in prompts.prompts], [str(r.uri) for r in resources.resources]
    tools, prompts, resources = asyncio.run(run())
    for name in ("cellar_summary", "search_wines", "get_wine", "find_bottles", "drinking_schedule", "list_storages",
                 "get_history", "list_wines_to_enrich", "get_enrichment_guide", "create_wine", "update_wine_info",
                 "set_drinking_window", "add_bottles", "remove_bottles", "move_bottle"):
        assert name in tools
    assert "enrich_wine" in prompts
    assert "cellarium://guides/wine-enrichment" in resources


def test_guide_rules(mcp):
    text = call(mcp, "get_enrichment_guide").content[0].text
    assert "Never invent" in text and "sources" in text


def test_enrichment_requires_sources_and_protects_user(mcp, conn):
    wid = cellar.create_wine(conn, {"name": "Bourseau (château)", "appellation": "Lalande de Pomerol", "type": "red",
                                    "region": "Bordeaux"})
    cellar.add_bottles(conn, wid, year=2010, quantity=2)
    res = call(mcp, "update_wine_info", {"wine_id": wid, "fields": {"grapes": "Merlot"}, "sources": []})
    assert res.is_error and "sources" in res.content[0].text
    res = call(mcp, "update_wine_info", {"wine_id": wid, "fields": {"notes": "x"}, "sources": ["https://a"]})
    assert res.is_error
    res = call(mcp, "update_wine_info", {"wine_id": wid, "fields": {"grapes": "Merlot", "region": "Libournais"},
                                         "sources": ["https://example.org/bourseau"]})
    out = payload(res)
    assert out["updated"] == ["grapes"] and out["skipped"][0]["field"] == "region"
    res = call(mcp, "set_drinking_window", {"wine_id": wid, "year": 2010, "drink_from": 2015, "peak_from": 2020,
                                            "peak_to": 2025, "drink_before": 2030,
                                            "sources": ["https://example.org/bourseau-2010"]})
    assert not res.is_error, res.content[0].text
    wine = cellar.get_wine(conn, wid)
    assert wine["vintages"][0]["drink_before"] == 2030
    assert wine["vintages"][0]["meta"]["drink_before"]["origin"] == "ai"
    todo = payload(call(mcp, "list_wines_to_enrich"))
    assert todo[0]["missing_fields"] == ["description", "producer"] or "description" in todo[0]["missing_fields"]


def test_stock_tools(mcp, conn):
    wid = cellar.create_wine(conn, {"name": "Les 3 Clochers", "appellation": "Coteaux du Layon", "type": "sweet"})
    rack = cellar.create_storage(conn, {"name": "Petit support", "kind": "rack", "rows": 3, "cols": 1})
    res = payload(call(mcp, "add_bottles", {"wine_id": wid, "quantity": 2, "year": 2012, "storage_id": rack}))
    assert res["placed"] == 2
    where = payload(call(mcp, "find_bottles", {"query": "Clochers"}))
    assert [b["location"]["label"] for b in where] == ["Petit support 1-I", "Petit support 2-I"]
    out = payload(call(mcp, "remove_bottles", {"wine_id": wid, "year": 2012, "count": 1, "note": "dessert"}))
    assert out[0]["status"] == "drunk"
    moved = payload(call(mcp, "move_bottle", {"bottle_id": where[1]["id"], "storage_id": rack, "row": 3, "col": 1}))
    assert moved["location"]["row"] == 3
    assert payload(call(mcp, "cellar_summary"))["by_type"]["sweet"] == 1


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_streamable_http_with_bearer(settings, conn):
    owner = auth.create_user(conn, "owner", "password1", "owner")
    reader = auth.create_user(conn, "reader", "password1", "reader")
    token = auth.create_token(conn, owner["id"], "test", "write")["token"]
    ro_token = auth.create_token(conn, reader["id"], "ro")["token"]
    app = create_app(settings)
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    try:
        import httpx
        from mcp.client.streamable_http import streamable_http_client

        url = f"http://127.0.0.1:{port}/mcp"
        assert httpx.post(url, json={}).status_code == 401

        async def run(tok, tool, args):
            async with httpx.AsyncClient(headers={"Authorization": f"Bearer {tok}"}) as http:
                async with Client(streamable_http_client(url, http_client=http)) as client:
                    tools = await client.list_tools()
                    result = await client.call_tool(tool, args)
                    return [t.name for t in tools.tools], result
        names, summary = asyncio.run(run(token, "cellar_summary", {}))
        assert "cellar_summary" in names and not summary.is_error
        _, created = asyncio.run(run(token, "create_wine", {"name": "Test", "type": "red", "provided_by_user": True}))
        assert not created.is_error, created.content
        wid = payload(created)["id"]
        _, added = asyncio.run(run(token, "add_bottles", {"wine_id": wid, "quantity": 1}))
        assert not added.is_error
        hist = cellar.history(conn)
        assert hist[0]["actor"] == "owner via MCP (test)"
        _, ro_read = asyncio.run(run(ro_token, "get_wine", {"wine_id": wid}))
        assert not ro_read.is_error
        _, ro_write = asyncio.run(run(ro_token, "add_bottles", {"wine_id": wid, "quantity": 1}))
        assert ro_write.is_error and "read-only" in ro_write.content[0].text
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_no_principal_is_refused(settings, conn):
    mcp = build_mcp(settings)  # no local principal and no HTTP headers
    res = call(mcp, "cellar_summary")
    assert res.is_error and "authentication" in res.content[0].text

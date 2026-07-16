import pytest


@pytest.mark.asyncio
async def test_create_and_list_configs(client):
    payload = {
        "title": "企业资质核验",
        "technique": "对照招标资格要求",
        "content_mode": "description",
        "content_scope": None,
        "content_text": "所有资质文件",
        "importance": "high",
    }
    r = await client.post("/api/configs", json=payload)
    assert r.status_code == 201
    body = r.json()
    assert body["title"] == "企业资质核验"
    assert body["id"] > 0

    r2 = await client.get("/api/configs")
    assert r2.status_code == 200
    assert len(r2.json()) == 1


@pytest.mark.asyncio
async def test_update_and_delete_config(client):
    r = await client.post(
        "/api/configs",
        json={
            "title": "目录",
            "technique": "检查目录",
            "content_mode": "full_text",
            "content_scope": "directory",
            "content_text": None,
            "importance": "medium",
        },
    )
    cid = r.json()["id"]
    r2 = await client.put(
        f"/api/configs/{cid}",
        json={
            "title": "目录完整性",
            "technique": "检查目录与正文",
            "content_mode": "full_text",
            "content_scope": "directory",
            "content_text": None,
            "importance": "high",
        },
    )
    assert r2.status_code == 200
    assert r2.json()["title"] == "目录完整性"

    r3 = await client.delete(f"/api/configs/{cid}")
    assert r3.status_code == 204
    assert (await client.get("/api/configs")).json() == []

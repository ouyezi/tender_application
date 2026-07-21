import io

import pytest


def _pdf_bytes():
    return b"%PDF-1.4 fake"


@pytest.mark.asyncio
async def test_create_task_requires_files(client):
    r = await client.post("/api/tasks", data={"background": "x"})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_create_and_get_task(client):
    # seed one config so snapshot non-empty
    await client.post(
        "/api/configs",
        json={
            "title": "资质",
            "technique": "查",
            "content_mode": "description",
            "content_text": "资质",
            "importance": "high",
        },
    )
    files = {
        "tender_file": ("tender.pdf", io.BytesIO(_pdf_bytes()), "application/pdf"),
        "bid_file": ("bid.docx", io.BytesIO(b"PK fake"), "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    }
    data = {"background": "市政项目", "requirements": "核资质"}
    r = await client.post("/api/tasks", data=data, files=files)
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "draft"
    assert body["tender_filename"] == "tender.pdf"
    assert body["progress_total"] == 0

    r2 = await client.get(f"/api/tasks/{body['id']}")
    assert r2.status_code == 200
    assert r2.json()["background"] == "市政项目"


@pytest.mark.asyncio
async def test_reject_bad_extension(client):
    files = {
        "tender_file": ("tender.txt", io.BytesIO(b"x"), "text/plain"),
        "bid_file": ("bid.docx", io.BytesIO(b"PK"), "application/octet-stream"),
    }
    r = await client.post("/api/tasks", data={}, files=files)
    assert r.status_code == 400

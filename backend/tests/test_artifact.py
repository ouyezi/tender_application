from pathlib import Path

from app.services import artifact


def test_ensure_artifact_dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(artifact, "UPLOAD_DIR", tmp_path)
    root = artifact.ensure_artifact_dirs("T-TEST-001")
    for name in ("document", "markdown", "image", "table", "json", "report", "other"):
        assert (root / name).is_dir()
    assert root == tmp_path / "T-TEST-001"


def test_move_into_document(tmp_path, monkeypatch):
    monkeypatch.setattr(artifact, "UPLOAD_DIR", tmp_path)
    task_id = "T-TEST-002"
    root = artifact.ensure_artifact_dirs(task_id)
    src = root / "tender.docx"
    src.write_bytes(b"fake")
    dest = artifact.move_into_document(task_id, src, file_id="fid01", original_name="招标.docx")
    assert dest.is_file()
    assert dest.parent == root / "document"
    assert "fid01" in dest.name
    assert not src.exists()


def test_sync_to_artifact_report(tmp_path, monkeypatch):
    monkeypatch.setattr(artifact, "UPLOAD_DIR", tmp_path)
    task_id = "T-TEST-004"
    src = tmp_path / "report.md"
    src.write_text("# report", encoding="utf-8")
    artifact.sync_to_artifact_report(task_id, src)
    dest = tmp_path / task_id / "report" / "report.md"
    assert dest.is_file()
    assert dest.read_text(encoding="utf-8") == "# report"


def test_write_index_md(tmp_path, monkeypatch):
    monkeypatch.setattr(artifact, "UPLOAD_DIR", tmp_path)
    task_id = "T-TEST-003"
    artifact.ensure_artifact_dirs(task_id)
    artifact.write_index_md(
        task_id,
        [
            {
                "file_id": "abc",
                "label": "招标文件",
                "original_filename": "a.docx",
                "kind": "document",
                "parse_status": "pending",
                "md_path": "",
                "tree_path": "",
                "warnings": "",
            }
        ],
    )
    text = (tmp_path / task_id / "index.md").read_text(encoding="utf-8")
    assert "abc" in text
    assert "招标文件" in text

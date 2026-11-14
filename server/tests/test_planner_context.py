import io
from datetime import datetime
from uuid import UUID
from zipfile import ZipFile

from campus import dto
from campus import models as m
from campus.planner.service import context_for


def test_model_gets_exact_inspected_evidence_sources_without_unselected_text(cp):
    data = io.BytesIO()
    with ZipFile(data, "w") as archive:
        archive.writestr("main.py", "print('hello')")
        archive.writestr("notes.txt", "unselected content must stay private")
    artifact = cp.upload(data.getvalue(), "project.zip", kind="project")
    preview = cp.call(
        "POST",
        "/job-previews",
        code=202,
        body={
            "prompt": "Run main.py",
            "project_artifact_id": artifact["id"],
            "selected_files": ["main.py"],
            "share_with_llm": True,
        },
    )
    try:
        with cp.runtime.sessions() as db:
            row = db.get(m.Preview, UUID(preview["id"]))
            user = db.get(m.User, row.owner_id)
            context = context_for(
                db, cp.runtime, row, user, dto.PreviewRequest.model_validate(row.request)
            )
        assert set(context["allowed_evidence_sources"]) == {"main.py", "request", "catalog", "inputs"}
        assert context["selected_files"] == {"main.py": "print('hello')"}
        assert "notes.txt" not in str(context)
        assert "/workspace/main.py" not in context["allowed_evidence_sources"]
        assert context["project_binding"] == {
            "artifact_id": artifact["id"],
            "mount_path": "/workspace",
            "archive": True,
        }
        assert (
            datetime.fromisoformat(context["default_job_deadline_at"])
            - datetime.fromisoformat(context["current_time"])
        ).total_seconds() == 1800
    finally:
        # The suite shares its DB schema but each harness has a separate storage root.
        cp.call("POST", f"/job-previews/{preview['id']}/cancel")

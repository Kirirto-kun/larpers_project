from __future__ import annotations

import hashlib
import os

import pytest

import campus
from conftest import Response


def artifact(path="model.pt", data=b"model weights", *, artifact_id="00000000-0000-4000-8000-000000000006", **overrides):
    return {"id": artifact_id, "sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data), "name": path, "media_type": "application/octet-stream", **overrides}


def outputs(api, artifacts):
    api.respond("GET", "/jobs/00000000-0000-4000-8000-000000000002", {"id": "00000000-0000-4000-8000-000000000002", "state": "succeeded", "artifacts": artifacts})


def test_download_verifies_every_artifact_then_atomically_publishes_directory(api, new_client, tmp_path):
    destination = tmp_path / "results"
    outputs(api, [artifact("model.pt"), artifact("metrics/质量.json", b'{"loss":0.1}', artifact_id="00000000-0000-4000-8000-000000000007")])
    api.respond("GET", "/artifacts/00000000-0000-4000-8000-000000000006/content", b"model weights")

    def metrics(_):
        assert not destination.exists(), "incomplete output must not become visible"
        return Response(body=b'{"loss":0.1}')

    api.handle("GET", "/artifacts/00000000-0000-4000-8000-000000000007/content", metrics)
    result = new_client(api).download("00000000-0000-4000-8000-000000000002", destination)
    assert result == destination
    assert (destination / "model.pt").read_bytes() == b"model weights"
    assert (destination / "metrics/质量.json").read_bytes() == b'{"loss":0.1}'
    assert sorted(str(p.relative_to(destination)).replace("\\", "/") for p in destination.rglob("*") if p.is_file()) == ["metrics/质量.json", "model.pt"]
    assert list(tmp_path.glob(".campus-download-*")) == []


@pytest.mark.parametrize("path", [
    "../escape", "/absolute", "a/../b", "a//b", "a/./b", "a\\b", "C:drive", "CON", "con.txt",
    "AUX.json", "COM1.log", "LPT¹", "trailing.", "trailing ", "dir /file", "file:stream", "a\x00b",
    "a\x01b", "a?b", "a*b", 'a"b', ".", "", "//server/share", "a/",
    "CON .txt", "CONIN$", "CONOUT$", "\ud800", "/".join(["deep"] * 65),
])
def test_download_rejects_unsafe_relative_paths_before_fetching_bytes(api, new_client, tmp_path, path):
    outputs(api, [artifact(path)])
    with pytest.raises(campus.UnsafePathError):
        new_client(api).download("00000000-0000-4000-8000-000000000002", tmp_path / "results")
    assert not api.seen("GET", "/artifacts/00000000-0000-4000-8000-000000000006/content")
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("paths", [
    ["Model.pt", "model.pt"], ["dir/file", "DIR/other"], ["dir", "dir/file"],
    ["a/b", "a"], ["é", "e\u0301"], ["duplicate", "duplicate"],
])
def test_download_rejects_colliding_file_or_directory_names(api, new_client, tmp_path, paths):
    outputs(api, [artifact(path, artifact_id=f"00000000-0000-4000-8000-{i + 20:012d}") for i, path in enumerate(paths)])
    with pytest.raises(campus.UnsafePathError):
        new_client(api).download("00000000-0000-4000-8000-000000000002", tmp_path / "results")
    assert not [r for r in api.requests if "/artifacts/" in r.path]
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("entries,options", [
    ([artifact(), artifact("second", artifact_id="00000000-0000-4000-8000-000000000008")], {"max_download_files": 1}),
    ([artifact()], {"max_download_bytes": 3}),
    ([artifact()], {"max_artifact_bytes": 3}),
    ([artifact(size_bytes=-1)], {}), ([artifact(size_bytes=True)], {}),
    ([artifact(sha256="0" * 63)], {}), ([artifact(artifact_id="../secret")], {}),
])
def test_download_enforces_manifest_size_count_digest_and_identity_bounds(api, new_client, tmp_path, entries, options):
    outputs(api, entries)
    with pytest.raises(campus.DownloadError):
        new_client(api, **options).download("00000000-0000-4000-8000-000000000002", tmp_path / "results")
    assert not [r for r in api.requests if "/artifacts/" in r.path]


def test_download_resumes_interrupted_response_using_verified_range(api, new_client, tmp_path):
    data = b"0123456789"
    outputs(api, [artifact(data=data)])

    def content(request):
        if request.headers.get("Range") is None:
            return Response(body=data, truncate=3)
        assert request.headers["Range"] == "bytes=3-"
        return Response(206, data[3:], {"Content-Range": "bytes 3-9/10"})

    api.handle("GET", "/artifacts/00000000-0000-4000-8000-000000000006/content", content)
    destination = new_client(api).download("00000000-0000-4000-8000-000000000002", tmp_path / "results")
    assert (destination / "model.pt").read_bytes() == data
    assert len(api.seen("GET", "/artifacts/00000000-0000-4000-8000-000000000006/content")) == 2


def test_download_restarts_safely_when_server_ignores_range(api, new_client, tmp_path):
    data = b"0123456789"
    outputs(api, [artifact(data=data)])
    calls = 0

    def content(_):
        nonlocal calls
        calls += 1
        return Response(body=data, truncate=3) if calls == 1 else Response(body=data)

    api.handle("GET", "/artifacts/00000000-0000-4000-8000-000000000006/content", content)
    destination = new_client(api).download("00000000-0000-4000-8000-000000000002", tmp_path / "results")
    assert (destination / "model.pt").read_bytes() == data
    assert calls == 2


@pytest.mark.parametrize("content_range", ["bytes 0-6/10", "bytes 3-9/99", "bytes 3-20/10", "invalid"])
def test_download_rejects_incorrect_resume_range(api, new_client, tmp_path, content_range):
    data = b"0123456789"
    outputs(api, [artifact(data=data)])
    calls = 0

    def content(_):
        nonlocal calls
        calls += 1
        return Response(body=data, truncate=3) if calls == 1 else Response(206, data[3:], {"Content-Range": content_range})

    api.handle("GET", "/artifacts/00000000-0000-4000-8000-000000000006/content", content)
    with pytest.raises(campus.DownloadError):
        new_client(api).download("00000000-0000-4000-8000-000000000002", tmp_path / "results")
    assert not (tmp_path / "results").exists()
    assert list(tmp_path.iterdir()) == []
    assert calls == 2


@pytest.mark.parametrize("response", [
    Response(body=b"corrupt bytes"), Response(body=b"model weights plus extra"),
    Response(body=b"model weights", headers={"Content-Encoding": "gzip"}),
])
def test_download_never_publishes_corrupt_oversized_or_encoded_artifacts(api, new_client, tmp_path, response):
    outputs(api, [artifact()])
    api.handle("GET", "/artifacts/00000000-0000-4000-8000-000000000006/content", lambda _: response)
    with pytest.raises(campus.DownloadError):
        new_client(api).download("00000000-0000-4000-8000-000000000002", tmp_path / "results")
    assert list(tmp_path.iterdir()) == []


def test_failed_second_artifact_does_not_publish_first_artifact(api, new_client, tmp_path):
    outputs(api, [artifact(), artifact("second", b"second", artifact_id="00000000-0000-4000-8000-000000000008")])
    api.respond("GET", "/artifacts/00000000-0000-4000-8000-000000000006/content", b"model weights")
    api.respond("GET", "/artifacts/00000000-0000-4000-8000-000000000008/content", b"broken")
    with pytest.raises(campus.IntegrityError):
        new_client(api).download("00000000-0000-4000-8000-000000000002", tmp_path / "results")
    assert list(tmp_path.iterdir()) == []


def test_auth_failure_during_resume_is_terminal_and_leaves_no_outputs(api, new_client, tmp_path):
    outputs(api, [artifact(data=b"0123456789")])
    calls = 0

    def content(_):
        nonlocal calls
        calls += 1
        return Response(body=b"0123456789", truncate=3) if calls == 1 else Response(403, {"code": "FORBIDDEN"})

    api.handle("GET", "/artifacts/00000000-0000-4000-8000-000000000006/content", content)
    with pytest.raises(campus.AuthenticationError):
        new_client(api).download("00000000-0000-4000-8000-000000000002", tmp_path / "results")
    assert calls == 2
    assert list(tmp_path.iterdir()) == []


def test_existing_destination_is_never_replaced(api, new_client, tmp_path):
    destination = tmp_path / "results"
    destination.mkdir()
    (destination / "keep").write_text("keep")
    outputs(api, [artifact()])
    with pytest.raises(FileExistsError):
        new_client(api).download("00000000-0000-4000-8000-000000000002", destination)
    assert (destination / "keep").read_text() == "keep"
    assert not [r for r in api.requests if "/artifacts/" in r.path]


@pytest.mark.parametrize("link_is_destination", [True, False])
def test_download_rejects_symlink_destination_or_ancestor(api, new_client, tmp_path, link_is_destination):
    outside = tmp_path / "outside"
    outside.mkdir()
    link = tmp_path / "link"
    try:
        os.symlink(outside, link, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"symlink creation is unavailable: {error}")
    outputs(api, [artifact()])
    with pytest.raises(campus.UnsafePathError):
        new_client(api).download("00000000-0000-4000-8000-000000000002", link if link_is_destination else link / "results")
    assert list(outside.iterdir()) == []


def test_artifact_redirect_is_not_followed(api, new_client, tmp_path):
    outputs(api, [artifact()])
    api.respond("GET", "/artifacts/00000000-0000-4000-8000-000000000006/content", {}, status=302, headers={"Location": "https://example.com/exfiltrate"})
    with pytest.raises(campus.APIError):
        new_client(api).download("00000000-0000-4000-8000-000000000002", tmp_path / "results")
    assert len(api.seen("GET", "/artifacts/00000000-0000-4000-8000-000000000006/content")) == 1
    assert list(tmp_path.iterdir()) == []


def test_complete_bytes_with_lost_chunk_terminator_are_still_verified(api, new_client, tmp_path):
    outputs(api, [artifact(data=b"0123456789")])
    api.handle("GET", "/artifacts/00000000-0000-4000-8000-000000000006/content", lambda _: Response(
        body=b"a\r\n0123456789\r\n", headers={"Transfer-Encoding": "chunked"}, omit_length=True, truncate=15,
    ))
    destination = new_client(api, max_attempts=1).download("00000000-0000-4000-8000-000000000002", tmp_path / "results")
    assert (destination / "model.pt").read_bytes() == b"0123456789"
    assert len(api.seen("GET", "/artifacts/00000000-0000-4000-8000-000000000006/content")) == 1


def test_concurrently_created_empty_destination_is_never_replaced(api, new_client, tmp_path):
    destination = tmp_path / "results"
    outputs(api, [artifact()])

    def content(_):
        destination.mkdir()
        return Response(body=b"model weights")

    api.handle("GET", "/artifacts/00000000-0000-4000-8000-000000000006/content", content)
    with pytest.raises(FileExistsError):
        new_client(api).download("00000000-0000-4000-8000-000000000002", destination)
    assert destination.is_dir()
    assert list(destination.iterdir()) == []
    assert list(tmp_path.glob(".campus-download-*")) == []

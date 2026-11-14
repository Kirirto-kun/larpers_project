import io
from zipfile import ZipFile

from conftest import checksum


def test_resumable_parts_and_finalize(cp):
    data = b"abcd1234"
    row = cp.call(
        "POST",
        "/uploads",
        body={
            "name": "файл с данными.txt",
            "size_bytes": len(data),
            "sha256": checksum(data),
            "part_size_bytes": 4,
        },
        code=201,
    )
    endpoint = f"/uploads/{row['id']}"
    cp.call("POST", endpoint + "/finalize", code=409)
    second = {**cp.owner, "X-Content-SHA256": checksum(data[4:])}
    cp.call("PUT", endpoint + "/parts/1", headers=second, content=data[4:])
    cp.call("PUT", endpoint + "/parts/1", headers=second, content=data[4:])
    cp.call(
        "PUT",
        endpoint + "/parts/1",
        headers={**cp.owner, "X-Content-SHA256": checksum(b"junk")},
        content=b"junk",
        code=409,
    )
    assert cp.call("GET", endpoint)["parts"][0]["number"] == 1
    cp.call(
        "PUT",
        endpoint + "/parts/0",
        headers={**cp.owner, "X-Content-SHA256": checksum(data[:4])},
        content=data[:4],
    )
    artifact = cp.call("POST", endpoint + "/finalize")
    assert cp.call("POST", endpoint + "/finalize") == artifact
    assert cp.call("GET", f"/artifacts/{artifact['id']}/content").content == data


def test_unsafe_zip_has_no_ready_artifact(cp):
    out = io.BytesIO()
    with ZipFile(out, "w") as archive:
        archive.writestr("../escape", "blocked")
    error = cp.upload(out.getvalue(), "project.zip", kind="project", code=422)
    assert error["code"] == "UNSAFE_PATH"

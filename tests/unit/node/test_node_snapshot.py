"""
Copyright (c) 2020 Aiven Ltd
See LICENSE for details
"""

from astacus.common import ipc, magic, utils
from astacus.common.progress import Progress
from astacus.common.snapshot import SnapshotGroup
from astacus.common.storage import JsonObject
from astacus.node.memory_snapshot import MemorySnapshot
from astacus.node.snapshot import Snapshot
from astacus.node.snapshot_op import SnapshotOp
from astacus.node.sqlite_snapshot import SQLiteSnapshot
from astacus.node.uploader import Uploader
from fastapi.testclient import TestClient
from pathlib import Path
from pytest_mock import MockerFixture
from tests.unit.node.conftest import build_snapshot_and_snapshotter, create_files_at_path

import os
import pytest


@pytest.mark.timeout(2)
@pytest.mark.parametrize("snapshot_cls", [MemorySnapshot, SQLiteSnapshot])
@pytest.mark.parametrize("src_is_dst", [True, False])
def test_snapshot(
    uploader: Uploader,
    snapshot_cls: type[Snapshot],
    src: Path,
    dst: Path,
    db: Path,
    src_is_dst: bool,
):
    if src_is_dst:
        dst = src

    snapshot, snapshotter = build_snapshot_and_snapshotter(src, dst, db, snapshot_cls, [SnapshotGroup("**")])
    with snapshotter.lock:
        # Start with empty
        snapshotter.perform_snapshot(progress=Progress())
        assert not (dst / "foo").is_file()

        # Create files in src, run snapshot
        create_files_at_path(
            src,
            [
                (Path("foo"), b"foo"),
                (Path("foo2"), b"foo2"),
                (Path("foobig"), b"foobig" * magic.DEFAULT_EMBEDDED_FILE_SIZE),
                (Path("foobig2"), b"foobig2" * magic.DEFAULT_EMBEDDED_FILE_SIZE),
            ],
        )
        snapshotter.perform_snapshot(progress=Progress())
        ss2 = snapshotter.get_snapshot_state()

        assert (dst / "foo").is_file()
        assert (dst / "foo").read_text() == "foo"
        assert (dst / "foo2").read_text() == "foo2"
        assert (dst / "foobig").read_text() == "foobig" * magic.DEFAULT_EMBEDDED_FILE_SIZE
        assert (dst / "foobig2").read_text() == "foobig2" * magic.DEFAULT_EMBEDDED_FILE_SIZE

        hashes = list(snapshot.get_all_digests())
        assert len(hashes) == 2
        assert hashes == [
            ipc.SnapshotHash(hexdigest="552d458198758daac752b948253b0d28bf0f21fcda40c626e54b5f9acf541a16", size=1200),
            ipc.SnapshotHash(hexdigest="d13d8d5177d72633707f17f1d7df9caa169aa87dbc9ee738cb05c5c373591375", size=1400),
        ]

        while True:
            (src / "foo").write_text("barfoo")  # same length
            snapshotter.perform_snapshot(progress=Progress())
            if snapshot.get_file(Path("foo")) is not None:
                # Sometimes fails on first iteration(s) due to same mtime
                # (inaccurate timestamps)
                break
        ss3 = snapshotter.get_snapshot_state()
        assert ss2 != ss3
        snapshotter.perform_snapshot(progress=Progress())
        assert (dst / "foo").is_file()
        assert (dst / "foo").read_text() == "barfoo"

        uploader.write_hashes_to_storage(snapshot=snapshot, hashes=hashes, parallel=1, progress=Progress())

        # Remove file from src, run snapshot
        for filename in ["foo", "foo2", "foobig", "foobig2"]:
            (src / filename).unlink()
            snapshotter.perform_snapshot(progress=Progress())
            assert not (dst / filename).is_file()

        # Now shouldn't have any data hashes
        hashes_empty = list(snapshot.get_all_digests())
        assert not hashes_empty


def test_api_snapshot_and_upload(client: TestClient, mocker: MockerFixture):
    url = "http://addr/result"
    m = mocker.patch.object(utils, "http_request")
    response = client.post("/node/snapshot")
    assert response.status_code == 422, response.json()

    req_json: JsonObject = {"root_globs": ["*"]}
    response = client.post("/node/snapshot", json=req_json)
    assert response.status_code == 409, response.json()

    response = client.post("/node/lock?locker=x&ttl=10")
    assert response.status_code == 200, response.json()
    response = client.post("/node/snapshot", json=req_json)
    assert response.status_code == 200, response.json()

    req_json["result_url"] = url
    response = client.post("/node/snapshot", json=req_json)
    assert response.status_code == 200, response.json()

    # Decode the (result endpoint) response using the model
    response = m.call_args[1]["data"]
    result = ipc.SnapshotResult.parse_raw(response)
    assert result.progress.finished_successfully
    assert result.hashes
    assert result.files
    assert result.total_size
    assert result.az

    # Ask it to be uploaded
    response = client.post("/node/upload")
    assert response.status_code == 422, response.json()
    response = client.post(
        "/node/upload", json={"storage": "x", "hashes": [x.dict() for x in result.hashes], "result_url": url}
    )
    assert response.status_code == 200, response.json()
    response = m.call_args[1]["data"]
    result2 = ipc.SnapshotUploadResult.parse_raw(response)
    assert result2.progress.finished_successfully


def test_api_snapshot_error(client, mocker):
    req_json = {"root_globs": ["*"]}
    response = client.post("/node/lock?locker=x&ttl=10")
    assert response.status_code == 200, response.json()
    response = client.post("/node/snapshot", json=req_json)
    assert response.status_code == 200, response.json()
    status_url = response.json()["status_url"]

    # Now, make sure if it fails it produces sane enough sounding result
    def _fun(self):
        raise ValueError("muah")

    mocker.patch.object(SnapshotOp, "perform_snapshot", new=_fun)
    with pytest.raises(ValueError):
        # The fact that it propagates here immediately kind of sucks
        response = client.post("/node/snapshot", json=req_json)
    assert status_url.endswith("/1")
    failed_op_status_url = status_url[:-2] + "/2"
    response = client.get(failed_op_status_url)
    assert response.status_code == 200, response.json()
    progress = response.json()["progress"]
    assert progress["failed"]
    assert progress["final"]


@pytest.mark.timeout(2)
@pytest.mark.parametrize(
    "truncate_to,hashes_in_second_snapshot",
    [
        (magic.DEFAULT_EMBEDDED_FILE_SIZE - 1, 0),
        (magic.DEFAULT_EMBEDDED_FILE_SIZE + 1, 1),
    ],
)
@pytest.mark.parametrize("snapshot_cls", [MemorySnapshot, SQLiteSnapshot])
@pytest.mark.parametrize("src_is_dst", [True, False])
def test_snapshot_file_size_changed(
    snapshot_cls: type[Snapshot],
    src: Path,
    dst: Path,
    db: Path,
    src_is_dst: bool,
    truncate_to,
    hashes_in_second_snapshot: int,
):
    if src_is_dst:
        dst = src

    snapshot, snapshotter = build_snapshot_and_snapshotter(src, dst, db, snapshot_cls, [SnapshotGroup("**")])
    path = src / "shrinky"
    with snapshotter.lock:
        path.write_text("foobar" * magic.DEFAULT_EMBEDDED_FILE_SIZE)
        snapshotter.perform_snapshot(progress=Progress())

        os.truncate(path, truncate_to)
        snapshotter.perform_snapshot(progress=Progress())
        assert len(list(snapshot.get_all_digests())) == hashes_in_second_snapshot

"""
Copyright (c) 2022 Aiven Ltd
See LICENSE for details
"""

from astacus.common import ipc
from astacus.common.cassandra.schema import CassandraSchema
from astacus.coordinator.plugins import base
from astacus.coordinator.plugins.cassandra import restore_steps
from astacus.coordinator.plugins.cassandra.model import CassandraManifest, CassandraManifestNode
from types import SimpleNamespace

import datetime
import pytest

# TBD: Eventually multinode configuration would be perhaps interesting to test too


@pytest.mark.parametrize("override_tokens", [False, True])
@pytest.mark.asyncio
async def test_step_start_cassandra(mocker, override_tokens):
    plugin_manifest = CassandraManifest(
        cassandra_schema=CassandraSchema(keyspaces=[]),
        nodes=[
            CassandraManifestNode(
                address="127.0.0.1",
                host_id="12345678123456781234567812345678",
                listen_address="::1",
                rack="unused",
                tokens=["token0"],
            )
        ],
    )

    backup_manifest = ipc.BackupManifest(
        start=datetime.datetime.now(),
        attempt=1,
        snapshot_results=[ipc.SnapshotResult()],
        upload_results=[],
        plugin=ipc.Plugin.cassandra,
        plugin_data=plugin_manifest.dict(),
    )

    def get_result(cl):
        if cl == base.BackupManifestStep:
            return backup_manifest
        if cl == restore_steps.ParsePluginManifestStep:
            return plugin_manifest
        raise NotImplementedError(cl)

    mocker.patch.object(restore_steps, "run_subop")

    step = restore_steps.StartCassandraStep(partial_restore_nodes=None, override_tokens=override_tokens)
    context = SimpleNamespace(get_result=get_result)
    cluster = SimpleNamespace(nodes=[SimpleNamespace(az="az1")])
    result = await step.run_step(cluster, context)
    assert result is None


class AsyncIterableWrapper:
    def __init__(self, iterable):
        self.data = list(iterable)
        self.index = -1

    def __aiter__(self):
        return self

    async def __anext__(self):
        self.index += 1
        if self.index >= len(self.data):
            raise StopAsyncIteration
        return self.data[self.index]


@pytest.mark.parametrize("steps,success", [([True], True), ([False, True], True), ([False], False)])
@pytest.mark.asyncio
async def test_step_wait_cassandra_up(mocker, steps, success):
    get_schema_steps = steps[:]

    async def get_schema_hash(cluster):
        assert get_schema_steps
        return get_schema_steps.pop(0), "unused-error"

    mocker.patch.object(restore_steps, "get_schema_hash", new=get_schema_hash)

    mocker.patch.object(restore_steps.utils, "exponential_backoff", return_value=AsyncIterableWrapper(steps))

    step = restore_steps.WaitCassandraUpStep(duration=123)
    context = None
    cluster = None
    if success:
        result = await step.run_step(cluster, context)
        assert result is None
    else:
        with pytest.raises(base.StepFailedError):
            await step.run_step(cluster, context)

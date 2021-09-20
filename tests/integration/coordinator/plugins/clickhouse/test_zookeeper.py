"""
Copyright (c) 2021 Aiven Ltd
See LICENSE for details
"""
from .conftest import Service
from astacus.common.utils import build_netloc
from astacus.coordinator.plugins.clickhouse.zookeeper import KazooZooKeeperClient, NodeExistsError, NoNodeError

import dataclasses
import kazoo.client
import pytest
import secrets
import time

pytestmark = [
    pytest.mark.clickhouse,
    pytest.mark.order("second_to_last"),
]


@dataclasses.dataclass
class ZNode:
    path: str
    content: bytes


def get_kazoo_host(zookeeper: Service) -> str:
    return build_netloc(zookeeper.host, zookeeper.port)


@pytest.fixture(name="zookeeper_client")
def fixture_zookeeper_client(zookeeper: Service) -> KazooZooKeeperClient:
    return KazooZooKeeperClient(hosts=[get_kazoo_host(zookeeper)], timeout=1)


@pytest.fixture(name="znode")
def fixture_znode(zookeeper: Service) -> ZNode:
    name = secrets.token_hex()
    znode = ZNode(path=f"/test/{name}", content=b"the_content")
    base_client = kazoo.client.KazooClient(hosts=[get_kazoo_host(zookeeper)])
    base_client.start()
    base_client.create(znode.path, znode.content, makepath=True)
    base_client.stop()
    base_client.close()
    return znode


@pytest.mark.asyncio
async def test_kazoo_zookeeper_client_get(zookeeper_client: KazooZooKeeperClient, znode: ZNode):
    async with zookeeper_client.connect() as connection:
        assert await connection.get(znode.path) == znode.content


@pytest.mark.asyncio
async def test_kazoo_zookeeper_client_get_missing_node_fails(zookeeper_client: KazooZooKeeperClient):
    async with zookeeper_client.connect() as connection:
        with pytest.raises(NoNodeError):
            assert await connection.get("/does/not/exist")


@pytest.mark.asyncio
async def test_kazoo_zookeeper_client_get_children(zookeeper_client):
    async with zookeeper_client.connect() as connection:
        assert await connection.get_children("/zookeeper") == ["config", "quota"]


@pytest.mark.asyncio
async def test_kazoo_zookeeper_client_get_children_of_missing_node_fails(zookeeper_client):
    async with zookeeper_client.connect() as connection:
        with pytest.raises(NoNodeError):
            assert await connection.get_children("/does/not/exists")


@pytest.mark.asyncio
async def test_kazoo_zookeeper_client_create(zookeeper_client):
    async with zookeeper_client.connect() as connection:
        assert await connection.create("/new/node", b"content")
        assert await connection.get("/new/node") == b"content"


@pytest.mark.asyncio
async def test_kazoo_zookeeper_client_create_existing_node_fails(zookeeper_client):
    async with zookeeper_client.connect() as connection:
        with pytest.raises(NodeExistsError):
            await connection.create("/zookeeper", b"content")


@pytest.mark.asyncio
async def test_kazoo_zookeeper_client_bounded_failure_time(
    zookeeper_client: KazooZooKeeperClient, zookeeper: Service, znode: ZNode
):
    async with zookeeper_client.connect() as connection:
        zookeeper.process.kill()
        start_time = time.monotonic()
        with pytest.raises(Exception):
            await connection.get(znode.path)
        elapsed_time = time.monotonic() - start_time
        # We allow for a bit of margin
        assert elapsed_time < 10.0

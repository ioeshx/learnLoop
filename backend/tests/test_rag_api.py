"""End-to-end local ingestion, hybrid retrieval, and Agent citation tests."""

import os
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

from app.application import SearchLearningResources
from app.config import Settings
from app.main import create_app
from app.workers.bootstrap import open_background_worker

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.asyncio
async def test_markdown_resource_is_deduplicated_searchable_and_recycled(
    tmp_path: Path,
) -> None:
    settings = Settings(
        environment="test",
        data_dir=tmp_path / "data",
        embedding_dimensions=64,
        resource_chunk_size=200,
        resource_chunk_overlap=30,
    )
    _migrate(settings)
    application = create_app(settings)

    async with application.router.lifespan_context(application):
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            goal_response = await client.post(
                "/api/v1/goals",
                headers={"Idempotency-Key": "rag-goal"},
                json={
                    "title": "图算法",
                    "description": "学习个人笔记中的图遍历",
                    "desired_outcome": "独立实现 BFS",
                    "weekly_minutes": 120,
                },
            )
            goal_id = goal_response.json()["id"]
            plan = (await client.post(f"/api/v1/goals/{goal_id}/plans")).json()
            node_id = plan["items"][0]["knowledge_node_id"]
            document = (
                "# 图算法手册\n\n"
                "## 图算法：基础与边界\n\n"
                "广度优先搜索 BFS 使用先进先出的队列，适合求无权图最短路径。\n\n"
                "## 深度优先搜索\n\nDFS 通常使用栈或递归。"
            ).encode()

            uploaded = await client.post(
                "/api/v1/resources/files",
                data={"goal_id": goal_id, "knowledge_node_id": node_id},
                files={"file": ("graphs.md", document, "text/markdown")},
            )
            assert uploaded.status_code == 202
            submission = uploaded.json()
            resource = submission["resource"]
            job = submission["job"]
            assert resource["status"] == "processing"
            assert job["status"] == "queued"
            assert resource["sha256"]

            async with open_background_worker(settings) as worker:
                completed = await worker.run_once()
            assert completed is not None
            assert completed.id == job["id"]
            assert completed.status.value == "succeeded"

            indexed = await client.get(f"/api/v1/resources/{resource['id']}")
            assert indexed.json()["status"] == "ready"
            completed_job = await client.get(f"/api/v1/jobs/{job['id']}")
            assert completed_job.json()["progress"] == 100

            duplicate = await client.post(
                "/api/v1/resources/files",
                data={"goal_id": goal_id, "knowledge_node_id": node_id},
                files={"file": ("copy.md", document, "text/markdown")},
            )
            assert duplicate.json()["resource"]["id"] == resource["id"]
            assert duplicate.json()["job"]["id"] == job["id"]

            searched = await client.get(
                "/api/v1/resources/search",
                params={
                    "query": "BFS 为什么使用队列",
                    "goal_id": goal_id,
                    "knowledge_node_id": node_id,
                },
            )
            assert searched.status_code == 200
            citations = searched.json()
            assert citations[0]["resource_id"] == resource["id"]
            assert citations[0]["locator"] == "图算法：基础与边界"
            assert "先进先出" in citations[0]["excerpt"]

            unsupported = await client.get(
                "/api/v1/resources/search",
                params={
                    "query": "量子色动力学夸克禁闭",
                    "goal_id": goal_id,
                    "knowledge_node_id": node_id,
                },
            )
            assert unsupported.json() == []

            snippets = await SearchLearningResources(
                application.state.application_dependencies
            ).execute(node_id)
            assert snippets
            assert snippets[0].chunk_id == citations[0]["chunk_id"]
            assert snippets[0].section == "图算法：基础与边界"

            listed = await client.get(
                "/api/v1/resources", params={"goal_id": goal_id}
            )
            assert [item["id"] for item in listed.json()] == [resource["id"]]

            deleted = await client.delete(
                f"/api/v1/resources/{resource['id']}"
            )
            assert deleted.status_code == 204
            missing = await client.get(f"/api/v1/resources/{resource['id']}")
            assert missing.status_code == 404

    trash = settings.document_storage_path / "trash"
    assert any(trash.iterdir())


def _migrate(settings: Settings) -> None:
    environment = os.environ.copy()
    environment["LEARNLOOP_ENVIRONMENT"] = settings.environment
    environment["LEARNLOOP_DATA_DIR"] = str(settings.data_dir)
    subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "scripts" / "migrate.py"), "upgrade"],
        cwd=PROJECT_ROOT,
        env=environment,
        check=True,
    )

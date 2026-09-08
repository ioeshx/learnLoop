"""Portable local-data export for backup and inspection."""

import json

from fastapi import APIRouter
from fastapi.responses import Response

from app.api.dependencies import ApplicationDependenciesDep
from app.application import ExportLearningData

router = APIRouter(prefix="/data")


@router.get("/export")
async def export_learning_data(
    dependencies: ApplicationDependenciesDep,
) -> Response:
    payload = await ExportLearningData(dependencies).execute()
    return Response(
        content=json.dumps(payload, ensure_ascii=False, indent=2),
        media_type="application/json",
        headers={
            "Content-Disposition": (
                'attachment; filename="learnloop-learning-data.json"'
            )
        },
    )

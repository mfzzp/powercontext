"""Task state is versioned by the real PowerContext ArtifactRepository and SQLite profile."""
import asyncio
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, Field
from powercontext.artifacts import Artifact, ArtifactDraft
from powercontext.builtin.persistence.artifacts import ArtifactRepository
from powercontext.builtin.persistence.errors import RepositoryNotFoundError
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.tables import SHARED_TABLES


class Progress(BaseModel):
    completed: list[str] = Field(default_factory=list)
    pending: list[str] = Field(default_factory=list)
    validation: dict = Field(default_factory=dict)
    source_versions: dict = Field(default_factory=dict)


class ProgressArtifact(Artifact[Progress]):
    family: ClassVar[str] = "sdd-progress"


class ProgressDraft(ArtifactDraft[Progress]):
    family: ClassVar[str] = "sdd-progress"


async def _access(path, project, task, content):
    config = SQLiteConfig(url="sqlite+aiosqlite:///" + Path(path).resolve().as_posix())
    async with SQLiteProfile.open(config, tables=SHARED_TABLES) as profile:
        repo = ArtifactRepository((ProgressArtifact,))
        async with profile.database.transaction() as connection:
            try:
                previous = await repo.latest(connection, project, "sdd-progress", task)
            except RepositoryNotFoundError:
                previous = None
            if content is None:
                return None if previous is None else previous.model_dump(mode="json")
            draft = ProgressDraft(content=Progress.model_validate(content))
            if previous is None:
                result = await repo.create(connection, project, task, draft)
            else:
                result = await repo.revise(connection, project, previous, draft)
            return result.model_dump(mode="json")


def state(path, project, task, content=None):
    return asyncio.run(_access(path, project, task, content))

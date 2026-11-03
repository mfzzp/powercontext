"""seekdb Memory candidates from one native full-text/vector statement."""

from __future__ import annotations

from pyobvector import VECTOR
from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.sql.elements import TextClause

from powercontext.builtin.artifacts.memory import (
    CapabilityNotSupportedError,
    EmbeddingProfile,
    MemorySearchChannels,
    MemorySearchRequest,
)
from powercontext.builtin.artifacts.memory.canonical import validate_embedding
from powercontext.builtin.persistence.memory_index import CompositeMemoryIndex, MemoryIndex, memory_channel_hits
from powercontext.builtin.persistence.oceanbase.memory_index import (
    _OCEANBASE_VECTOR_SEARCH_SQL,
    OceanBaseMemoryFTSIndex,
    OceanBaseMemoryVectorIndex,
)
from powercontext.builtin.persistence.tags import memory_tag_parameters, memory_tag_sql

_FTS_CANDIDATES = """
SELECT
    f.memory_artifact_id,
    f.head_revision,
    f.entry_id,
    f.entry_version_id,
    v.text,
    MATCH(f.searchable_text) AGAINST (:analyzed_query) AS lexical_score
FROM pc_memory_entry_heads AS f
JOIN pc_memory_entry_versions AS v
  ON v.scope_id = f.scope_id
 AND v.memory_artifact_id = f.memory_artifact_id
 AND v.entry_version_id = f.entry_version_id
WHERE f.scope_id = :scope_id
  AND f.memory_artifact_id IN :memory_artifact_ids
  AND MATCH(f.searchable_text) AGAINST (:analyzed_query)
{tags}
ORDER BY lexical_score DESC, f.memory_artifact_id, f.entry_id, f.entry_version_id
LIMIT :candidate_limit
"""


def _hybrid_statement(profile: EmbeddingProfile, *, tagged: bool) -> TextClause:
    fts = _FTS_CANDIDATES.format(tags=memory_tag_sql("f") if tagged else "")
    vector = _OCEANBASE_VECTOR_SEARCH_SQL
    if tagged:
        vector = vector.replace("ORDER BY", memory_tag_sql("m") + "ORDER BY")
    # Each branch retains its own indexed ranking and LIMIT. UNION ALL is
    # intentional: a shared identity must still participate in both channels.
    statement = text(f"""
SELECT candidates.*
FROM (
    SELECT 'fts' AS channel, f.memory_artifact_id, f.head_revision,
           f.entry_id, f.entry_version_id, f.text, f.lexical_score,
           NULL AS distance
    FROM ({fts}) AS f
    UNION ALL
    SELECT 'vector' AS channel, m.memory_artifact_id, m.head_revision,
           m.entry_id, m.entry_version_id, m.text, NULL AS lexical_score,
           m.distance
    FROM ({vector}) AS m
) AS candidates
ORDER BY channel, lexical_score DESC, distance,
         memory_artifact_id, entry_id, entry_version_id
""")
    parameters = [
        bindparam("memory_artifact_ids", expanding=True),
        bindparam("query_vector", type_=VECTOR(profile.dimension)),
    ]
    if tagged:
        parameters.extend((bindparam("tag_keys", expanding=True), bindparam("tag_hashes", expanding=True)))
    return statement.bindparams(*parameters)


class SeekDBMemoryIndex(CompositeMemoryIndex):
    """Reuse native projection maintenance and single-channel behavior.

    Only the hybrid candidate read is replaced. Authoritative head validation,
    incremental projections, exact entry expansion, and rank fusion remain in
    their existing owners. Projection head_revision is not an active-row filter.
    """

    def __init__(self, profile: EmbeddingProfile | None = None) -> None:
        self._vector = None if profile is None else OceanBaseMemoryVectorIndex(profile)
        indexes: list[MemoryIndex] = [OceanBaseMemoryFTSIndex()]
        if self._vector is not None:
            indexes.append(self._vector)
        super().__init__(*indexes)
        self._hybrid_sql = None if profile is None else _hybrid_statement(profile, tagged=False)
        self._tagged_hybrid_sql = None if profile is None else _hybrid_statement(profile, tagged=True)

    async def search(
        self,
        connection: AsyncConnection,
        scope_id: str,
        request: MemorySearchRequest,
        /,
    ) -> MemorySearchChannels:
        if request.mode != "hybrid":
            return await super().search(connection, scope_id, request)
        vector = self._vector
        if vector is None or request.embedding_profile != vector.profile or request.query_vector is None:
            raise CapabilityNotSupportedError("embedding-profile")
        if not await vector.vector_complete(connection, scope_id, request.memories, vector.profile):
            raise CapabilityNotSupportedError("vector")
        statement = self._hybrid_sql if request.tag_filter is None else self._tagged_hybrid_sql
        if statement is None:
            raise CapabilityNotSupportedError("hybrid")
        rows = (
            await connection.execute(statement, {
                "scope_id": scope_id,
                "memory_artifact_ids": tuple(memory.artifact_id for memory in request.memories),
                "analyzed_query": request.analyzed_query,
                "query_vector": validate_embedding(request.query_vector, dimension=vector.profile.dimension),
                "candidate_limit": request.candidate_limit,
                **memory_tag_parameters(request.tag_filter),
            })
        ).mappings().all()
        return MemorySearchChannels(
            fts=memory_channel_hits((row for row in rows if row["channel"] == "fts"), request.memories),
            vector=memory_channel_hits((row for row in rows if row["channel"] == "vector"), request.memories),
        )

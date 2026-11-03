"""Adapter checks; these do not replace native seekdb SQL/EXPLAIN acceptance."""

from __future__ import annotations

import pytest
from sqlalchemy.dialects.mysql import dialect

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.memory import CapabilityNotSupportedError, EmbeddingProfile, MemorySearchRequest
from powercontext.builtin.artifacts.memory.canonical import embedding_content_hash
from powercontext.builtin.artifacts.memory.fusion import fuse_rankings
from powercontext.builtin.persistence.seekdb.memory_index import SeekDBMemoryIndex
from powercontext.builtin.tags import TagFilter

PROFILE = EmbeddingProfile(profile_id="hybrid-test", model="local", dimension=3, distance="l2", normalization="unit")
HEAD = ArtifactRef(family="memory", artifact_id="memory", revision=7)


def request(**changes):
    return MemorySearchRequest(**(dict(
        query="alpha", analyzed_query="alpha", memories=(HEAD,), mode="hybrid", candidate_limit=10,
        query_vector=(1.0, 0.0, 0.0), embedding_profile=PROFILE,
    ) | changes))


def row(entry="entry-a", version="version-a", channel="fts", distance=None):
    # An unchanged active projection deliberately carries an older head stamp.
    return dict(memory_artifact_id="memory", head_revision=2, entry_id=entry, entry_version_id=version,
                text=f"Alpha evidence {entry}", channel=channel, distance=distance, lexical_score=1.0)


class Result:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self

    def all(self):
        return self.rows

    def __iter__(self):
        return iter(self.rows)


class RecordingConnection:
    """Return database-shaped rows and record the actual supplied statements."""

    def __init__(self, rows=(), *, complete=True, revision=7):
        self.rows = rows
        self.complete = complete
        self.revision = revision
        self.candidates = []
        self.metadata_reads = []

    async def scalar(self, statement, parameters=None):
        self.metadata_reads.append(statement)
        return self.revision

    async def execute(self, statement, parameters=None):
        sql = str(statement.compile(dialect=dialect()))
        if "AGAINST" in sql or "l2_distance" in sql:
            self.candidates.append((statement, parameters))
            return Result(self.rows)
        self.metadata_reads.append(statement)
        if "pc_memory_vector_entries" in sql:
            digest = embedding_content_hash(
                profile_id=PROFILE.profile_id, model=PROFILE.model, dimension=PROFILE.dimension,
                distance=PROFILE.distance, normalization=PROFILE.normalization, entry_content_hash="a" * 64,
            )
            return Result([("entry-a", "version-a", "a" * 64, digest)] if self.complete else [])
        return Result([("entry-a", "version-a", "a" * 64)])


@pytest.mark.parametrize("tag_filter", [None, TagFilter(tags=("selected", "verified"), match="all"),
                                       TagFilter(tags=("selected", "verified"), match="any")])
async def test_hybrid_uses_one_parameterized_candidate_statement_with_both_filtered_limits(tag_filter):
    index = SeekDBMemoryIndex(PROFILE)
    connection = RecordingConnection((row(), row(channel="vector", distance=0.0)))
    malicious_query = "alpha' UNION SELECT secret --"
    result = await index.search(connection, "scope-a", request(analyzed_query=malicious_query, tag_filter=tag_filter))
    assert len(connection.candidates) == 1
    statement, parameters = connection.candidates[0]
    compiled = statement.params(**parameters).compile(dialect=dialect(), compile_kwargs={"render_postcompile": True})
    sql = str(compiled)
    assert "UNION ALL" in sql and "MATCH(" in sql and "l2_distance" in sql
    assert sql.count("LIMIT") == 2
    assert "f.scope_id = %s" in sql and "m.scope_id = %s" in sql
    assert "f.memory_artifact_id IN (%s)" in sql and "m.memory_artifact_id IN (%s)" in sql
    assert "APPROXIMATE" in sql
    assert malicious_query not in sql
    assert parameters["analyzed_query"] == malicious_query
    assert parameters["scope_id"] == "scope-a"
    assert parameters["candidate_limit"] == 10
    assert parameters["memory_artifact_ids"] == ("memory",)
    assert parameters["query_vector"] == (1.0, 0.0, 0.0)
    if tag_filter is not None:
        assert sql.count("FROM pc_artifact_tags AS tags") == 2
        assert "tags.target_id = f.entry_id" in sql and "tags.target_id = m.entry_id" in sql
        assert parameters["tag_minimum"] == (2 if tag_filter.match == "all" else 1)
        assert len(parameters["tag_hashes"]) == 2
    assert result.fts[0].memory_ref == HEAD
    assert result.vector[0].memory_ref == HEAD
    hits = fuse_rankings(fts=result.fts, vector=result.vector, limit=10)
    assert len(hits) == 1
    assert hits[0].matched_by == ("fts", "vector")
    assert hits[0].entry_version_id == "version-a"
    assert hits[0].text == "Alpha evidence entry-a"


async def test_channel_order_and_exact_identity_are_preserved_in_current_head():
    rows = (row("entry-b", "version-b"), row(), row(channel="vector", distance=0.1),
            row("entry-b", "version-b", "vector", 0.4))
    connection = RecordingConnection(rows)
    index = SeekDBMemoryIndex(PROFILE)
    first = await index.search(connection, "scope-a", request())
    second = await index.search(connection, "scope-a", request())
    assert first == second
    assert [hit.entry_id for hit in first.fts] == ["entry-b", "entry-a"]
    assert [hit.entry_id for hit in first.vector] == ["entry-a", "entry-b"]
    assert all(hit.memory_ref.revision == 7 for hit in (*first.fts, *first.vector))
    assert len(fuse_rankings(fts=first.fts, vector=first.vector, limit=10)) == 2


@pytest.mark.parametrize("mode", ["fts", "vector"])
async def test_single_channel_keeps_existing_native_path(mode):
    connection = RecordingConnection((row(distance=0.0 if mode == "vector" else None),))
    result = await SeekDBMemoryIndex(PROFILE).search(connection, "scope-a", request(mode=mode))
    assert len(connection.candidates) == 1
    sql = str(connection.candidates[0][0].compile(dialect=dialect()))
    assert "UNION ALL" not in sql
    assert bool(result.fts) == (mode == "fts")
    assert bool(result.vector) == (mode == "vector")


@pytest.mark.parametrize("settings", [dict(complete=False), dict(revision=6)])
async def test_incomplete_vectors_and_stale_head_fail_before_candidates(settings):
    connection = RecordingConnection(**settings)
    with pytest.raises(CapabilityNotSupportedError):
        await SeekDBMemoryIndex(PROFILE).search(connection, "scope-a", request())
    assert not connection.candidates


@pytest.mark.parametrize("changes", [dict(query_vector=None), dict(embedding_profile=None)])
async def test_invalid_embedding_profile_does_not_query_candidates(changes):
    connection = RecordingConnection()
    with pytest.raises(CapabilityNotSupportedError):
        await SeekDBMemoryIndex(PROFILE).search(connection, "scope-a", request(**changes))
    assert not connection.candidates


async def test_fts_only_configuration_still_works():
    index = SeekDBMemoryIndex()
    connection = RecordingConnection((row(),))
    result = await index.search(connection, "scope-a", request(mode="fts", embedding_profile=None, query_vector=None))
    assert result.fts and not result.vector
    assert index.capabilities.fts and not index.capabilities.hybrid
    with pytest.raises(CapabilityNotSupportedError):
        await index.search(connection, "scope-a", request())


async def test_empty_candidate_channels_still_use_one_statement():
    connection = RecordingConnection()
    result = await SeekDBMemoryIndex(PROFILE).search(connection, "scope-a", request())
    assert not result.fts and not result.vector
    assert len(connection.candidates) == 1

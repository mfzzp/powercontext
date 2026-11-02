"""PDF parsing and real public Source contracts; no FinanceBench gold data."""

from __future__ import annotations

import hashlib
import io
import unittest
from types import SimpleNamespace

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from powercontext.sources import (
    TEXT_EVIDENCE_PROJECTION_KEY,
    SourceDefinitionRegistry,
    SourceObservation,
    manifest_for_definition,
    project_source_for_transport,
)
from powercontext.sources.pdf_pages import (
    PDF_PAGE_SOURCE_DEFINITION,
    PDFDocumentInput,
    PDFPageInput,
    parse_pdf_document,
    search_pdf_pages,
)


def pdf_bytes(texts, *, password=None, title=None):
    writer = PdfWriter()
    for text in texts:
        page = writer.add_blank_page(width=612, height=792)
        if text is not None:
            font = DictionaryObject({
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            })
            page[NameObject("/Resources")] = DictionaryObject({
                NameObject("/Font"): DictionaryObject({NameObject("/F1"): font}),
            })
            stream = DecodedStreamObject()
            escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            stream.set_data(f"BT /F1 12 Tf 50 700 Td ({escaped}) Tj ET".encode("ascii"))
            page[NameObject("/Contents")] = stream
    if title:
        writer.add_metadata({"/Title": title})
    if password is not None:
        writer.encrypt(user_password=password, owner_password="owner")
    result = io.BytesIO()
    writer.write(result)
    return result.getvalue()


class PDFParserTests(unittest.TestCase):
    def test_all_physical_pages_text_numbers_and_blank_page(self):
        raw = pdf_bytes(["Sales 001.20 and costs 12,345", None, "Last page"])
        pages = parse_pdf_document(PDFDocumentInput(document_id="Example", content=raw))
        self.assertEqual([p.page_number for p in pages], [1, 2, 3])
        self.assertIn("001.20", pages[0].text)
        self.assertIn("12,345", pages[0].text)
        self.assertEqual(pages[1].text, "")
        self.assertEqual(pages[2].text, "Last page")
        self.assertEqual({p.document_sha256 for p in pages}, {hashlib.sha256(raw).hexdigest()})

    def test_empty_password_is_allowed_but_password_required_is_rejected(self):
        pages = parse_pdf_document(PDFDocumentInput(document_id="permissions", content=pdf_bytes([None], password="")))
        self.assertEqual(len(pages), 1)
        with self.assertRaises(ValueError):
            parse_pdf_document(PDFDocumentInput(document_id="private", content=pdf_bytes([None], password="secret")))

    def test_corrupt_and_blank_ids_fail(self):
        valid = pdf_bytes(["first", "second"])
        for content in (b"", b"not PDF", b"%PDF-1.7\ntruncated", valid[:len(valid) // 2]):
            with self.subTest(content=content[:20]), self.assertRaises(ValueError):
                parse_pdf_document(PDFDocumentInput(document_id="d", content=content))
        for identifier in ("", "  ", "\t\n"):
            with self.subTest(identifier=identifier), self.assertRaises(ValueError):
                parse_pdf_document(PDFDocumentInput(document_id=identifier, content=valid))


class PDFSourceTests(unittest.IsolatedAsyncioTestCase):
    async def test_exact_projection_roundtrip_and_revisions(self):
        registry = SourceDefinitionRegistry((PDF_PAGE_SOURCE_DEFINITION,))
        raw = pdf_bytes(["Revenue 123"])
        page = parse_pdf_document(PDFDocumentInput(document_id="Company 2022", content=raw))[0]
        source = await registry.resolve(page)
        self.assertEqual(await registry.read(source), page)
        self.assertLessEqual(len(source.name), 256)
        self.assertEqual((await registry.resolve(page)).name, source.name)
        observation = project_source_for_transport(registry, source)
        self.assertEqual(observation.definition_fingerprint, manifest_for_definition(PDF_PAGE_SOURCE_DEFINITION).fingerprint)
        restored = SourceObservation.model_validate_json(observation.model_dump_json())
        evidence = registry.project(restored, TEXT_EVIDENCE_PROJECTION_KEY)
        self.assertEqual(evidence["content"], "Company 2022\nRevenue 123")
        self.assertEqual(evidence["metadata"], {
            "document_id": page.document_id, "document_sha256": page.document_sha256, "page_number": 1,
        })
        revised = parse_pdf_document(PDFDocumentInput(document_id="Company 2022", content=pdf_bytes(["Revenue 123"], title="v2")))[0]
        self.assertNotEqual((await registry.resolve(revised)).name, source.name)
        self.assertEqual(await registry.read(source), page)

    async def test_long_ids_and_invalid_page_metadata(self):
        registry = SourceDefinitionRegistry((PDF_PAGE_SOURCE_DEFINITION,))
        page = PDFPageInput(document_id="长" * 1000, document_sha256="a" * 64, page_number=1, text="")
        self.assertLessEqual(len((await registry.resolve(page)).name), 256)
        for update in ({"page_number": 0}, {"document_sha256": "A" * 64}, {"document_id": " "}):
            with self.subTest(update=update), self.assertRaises(ValueError):
                await registry.resolve(page.model_copy(update=update))


class PDFSearchTests(unittest.IsolatedAsyncioTestCase):
    def memory(self, texts, *, source_ids=None):
        from powercontext.artifacts import ArtifactRef
        from powercontext.builtin.artifacts.memory.models import MemoryEntryVersion
        from powercontext.builtin.runtime import MemoryEntryRecord
        from powercontext.sources import SourceRef

        ref = ArtifactRef(family="memory", artifact_id="memory-1", revision=1)
        records = []
        for index, text in enumerate(texts):
            source_id = str(index) if source_ids is None else source_ids[index]
            entry = MemoryEntryVersion(
                memory_artifact_id="memory-1", entry_id=f"entry-{index}", entry_version_id=f"version-{index}",
                version=1, previous_version_id=None, kind="source-evidence", text=text,
                entry_content_hash="hash", created_in_revision=1,
                sources=(SourceRef(source_type="pdf-page", source_id=source_id),),
            )
            records.append(MemoryEntryRecord(memory_ref=ref, state="active", entry=entry))

        class Memory:
            def __init__(self):
                self.searches = []
                self.reads = []

            async def search(self, request):
                self.searches.append(request)
                return SimpleNamespace(hits=())

            async def list(self):
                return SimpleNamespace(entries=tuple(records))

            async def get(self, request):
                self.reads.append(request.citation)
                return next(record for record in records if record.citation == request.citation)

        return Memory(), records

    async def test_natural_language_offline_ranking_and_exact_citations(self):
        memory, records = self.memory([
            "ACME_2022_10K\nConsolidated statement of cash flows depreciation and amortization 120 capital expenditures 40",
            "ACME_2022_10K\nLegal proceedings. The company faces ongoing litigation and lawsuits.",
            "OTHER_2022_10K\nLegal proceedings litigation lawsuits material legal contingencies.",
        ])
        citations = await search_pdf_pages(memory, "What legal battles does Acme face?", limit=1)
        self.assertEqual(citations, (records[1].citation,))
        self.assertEqual(memory.searches[0].mode, "fts")
        self.assertEqual(memory.reads, list(citations))

    async def test_limit_zero_empty_scope_and_duplicate_pages(self):
        memory, _ = self.memory([])
        self.assertEqual(await search_pdf_pages(memory, "revenue", 20), ())
        self.assertEqual(await search_pdf_pages(memory, "revenue", 0), ())
        memory, _ = self.memory(["D\nRevenue 10", "D\nRevenue 10", "D\nCost 2"], source_ids=["same", "same", "other"])
        self.assertEqual(len(await search_pdf_pages(memory, "revenue", 20)), 2)
        self.assertEqual(len(await search_pdf_pages(memory, "revenue", 1)), 1)
        for limit in (-1, True, 1.5):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                await search_pdf_pages(memory, "revenue", limit)


if __name__ == "__main__":
    unittest.main(verbosity=2)

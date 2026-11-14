from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')

class Block(Strict):
    id: str
    text: str
    location: str
    kind: Literal['paragraph', 'table_row', 'header', 'footer', 'note'] = 'paragraph'

class Document(Strict):
    sha256: str
    blocks: list[Block]
    warnings: list[str] = Field(default_factory=list)

Category = Literal['contradiction', 'competition', 'characteristics', 'classification', 'warranty', 'standards', 'completeness', 'acceptance', 'other']

class Anchor(Strict):
    block_id: str
    quote: str = Field(min_length=1, max_length=6000)

class Citation(Strict):
    source_id: str
    quote: str = Field(min_length=8, max_length=2000, description='Точная цитата; для обозначения стандарта или кода допустим полный идентификатор.')

class Edit(Strict):
    block_id: str
    before: str = Field(min_length=1, max_length=4000)
    after: str = Field(min_length=1, max_length=6000)
    _review_reason: str = PrivateAttr(default='')

class RowInsertion(Strict):
    after_block_id: str
    cells: list[str] = Field(min_length=2, max_length=20)
    _review_reason: str = PrivateAttr(default='')

class Finding(Strict):
    category: Category
    severity: Literal['high', 'medium', 'low']
    status: Literal['document_issue', 'needs_review'] = 'needs_review'
    title: str = Field(min_length=5, max_length=250)
    anchors: list[Anchor] = Field(min_length=1, max_length=8)
    explanation: str = Field(min_length=10, max_length=4000)
    recommendation: str = Field(min_length=10, max_length=4000)
    basis: Literal['document', 'external'] = 'document'
    citations: list[Citation] = Field(default_factory=list, max_length=5)
    edits: list[Edit] = Field(default_factory=list, max_length=6)
    row_insertions: list[RowInsertion] = Field(default_factory=list, max_length=3)

COVERAGE_TOPICS = ('object', 'numbers', 'scope', 'warranty_acceptance', 'classification', 'standards', 'language')

class Coverage(Strict):
    topic: Literal['object', 'numbers', 'scope', 'warranty_acceptance', 'classification', 'standards', 'language']
    block_ids: list[str] = Field(max_length=30)
    result: Literal['checked', 'not_applicable', 'unverified']
    note: str = Field(min_length=10, max_length=800)

class Review(Strict):
    summary: str = Field(min_length=10, max_length=4000)
    findings: list[Finding] = Field(max_length=80)
    coverage: list[Coverage] = Field(default_factory=list, max_length=7)

class Source(Strict):
    id: str
    url: str
    title: str
    text: str
    retrieved_at: str
    sha256: str
    truncated: bool = False
    content_type: str = 'text/html'
    retrieval_method: Literal['direct', 'search_excerpt'] = 'direct'

class Report(Strict):
    schema_version: str = '1.0'
    document_sha256: str
    mode: Literal['offline', 'openrouter']
    model: str | None = None
    review: Review
    sources: list[Source] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    stats: dict = Field(default_factory=dict)
    changes: list[dict] = Field(default_factory=list)

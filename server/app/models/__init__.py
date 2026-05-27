"""Domain model layer — frozen dataclass 기반 도메인 entity.

`models/` 는 운영 도메인 entity (DB row 의 in-memory representation), `schemas/`
는 wire-level (FastAPI request/response Pydantic) 와 분리. ARCHITECTURE.md
line 132-136 의 layer 분리 일관.
"""

from app.models.source_citation import (
    CitationProducer,
    SourceCitation,
    SourceCitationError,
    SourceKind,
)

__all__ = [
    "CitationProducer",
    "SourceCitation",
    "SourceCitationError",
    "SourceKind",
]

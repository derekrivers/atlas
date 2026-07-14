"""Learning-system extraction public surface."""

from atlas.learning.extractor import (
    DEFAULT_RAW_DIFF_SIZE_CAP_BYTES,
    ExtractionTrigger,
    LessonExtractionError,
    LessonModelClient,
    assemble_evidence_bundle,
    extract_lesson_for_ticket,
    notable_done_ticket,
    render_extraction_prompt,
)

__all__ = [
    "DEFAULT_RAW_DIFF_SIZE_CAP_BYTES",
    "ExtractionTrigger",
    "LessonExtractionError",
    "LessonModelClient",
    "assemble_evidence_bundle",
    "extract_lesson_for_ticket",
    "notable_done_ticket",
    "render_extraction_prompt",
]

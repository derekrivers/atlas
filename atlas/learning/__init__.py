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
from atlas.learning.scheduler import (
    DEFAULT_INTERVAL_SECONDS as DEFAULT_LESSON_SCHEDULER_INTERVAL_SECONDS,
)
from atlas.learning.scheduler import (
    LessonSchedulerConfig,
    ScheduledExtraction,
    find_tickets_needing_extraction,
    run_poll_cycle,
)
from atlas.learning.scheduler import (
    run_scheduler as run_lesson_scheduler,
)

__all__ = [
    "DEFAULT_LESSON_SCHEDULER_INTERVAL_SECONDS",
    "DEFAULT_RAW_DIFF_SIZE_CAP_BYTES",
    "ExtractionTrigger",
    "LessonExtractionError",
    "LessonModelClient",
    "LessonSchedulerConfig",
    "ScheduledExtraction",
    "assemble_evidence_bundle",
    "extract_lesson_for_ticket",
    "find_tickets_needing_extraction",
    "notable_done_ticket",
    "render_extraction_prompt",
    "run_lesson_scheduler",
    "run_poll_cycle",
]

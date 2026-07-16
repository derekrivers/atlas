"""Learning-system public surface."""

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
from atlas.learning.patterns import (
    PATTERN_THRESHOLD,
    PatternCandidate,
    PatternCandidateSource,
    detect_pattern_candidates,
)
from atlas.learning.report import (
    ActiveCitationCount,
    CategoryStatusCount,
    LessonDwellBreach,
    LessonsReport,
    LessonSummary,
    StatusLessonGroup,
    TagLessonGroup,
    build_lessons_report,
    lessons_report_json,
    render_lessons_report_markdown,
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
    "PATTERN_THRESHOLD",
    "ActiveCitationCount",
    "CategoryStatusCount",
    "ExtractionTrigger",
    "LessonDwellBreach",
    "LessonExtractionError",
    "LessonModelClient",
    "LessonSchedulerConfig",
    "LessonSummary",
    "LessonsReport",
    "PatternCandidate",
    "PatternCandidateSource",
    "ScheduledExtraction",
    "StatusLessonGroup",
    "TagLessonGroup",
    "assemble_evidence_bundle",
    "build_lessons_report",
    "detect_pattern_candidates",
    "extract_lesson_for_ticket",
    "find_tickets_needing_extraction",
    "lessons_report_json",
    "notable_done_ticket",
    "render_extraction_prompt",
    "render_lessons_report_markdown",
    "run_lesson_scheduler",
    "run_poll_cycle",
]

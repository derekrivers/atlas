"""Persistence seam for pure PM admission evaluations."""

from atlas.core.models import AdmissionRun
from atlas.storage import AdmissionRunRepo, Database


def record_admission_run(database: Database, run: AdmissionRun) -> AdmissionRun:
    """Append a returned admission run without recalculating or mutating it."""

    return AdmissionRunRepo(database).record(run)

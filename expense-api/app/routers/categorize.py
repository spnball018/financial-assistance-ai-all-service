from typing import Optional

from fastapi import APIRouter, Depends

from app.db import get_engine
from categorize.ai import Classifier, classifier_from_env
from categorize.service import categorize_pending

router = APIRouter(tags=["categorize"])


def get_classifier() -> Optional[Classifier]:
    return classifier_from_env()


@router.post("/categorize")
def categorize(classifier: Optional[Classifier] = Depends(get_classifier)):
    """Categorize pending leaf transactions that have no category yet (memory, then AI)."""
    return categorize_pending(get_engine(), classifier)

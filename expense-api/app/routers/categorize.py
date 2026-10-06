from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from app.auth import current_user_id
from app.db import get_engine
from categorize.ai import Classifier, classifier_from_env
from categorize.service import categorize_pending

router = APIRouter(tags=["categorize"])


def get_classifier() -> Optional[Classifier]:
    try:
        return classifier_from_env()
    except RuntimeError as e:  # misconfigured CATEGORIZER; messages name env vars only, never values
        raise HTTPException(status_code=500, detail=f"categorizer misconfigured: {e}")


@router.post("/categorize")
def categorize(classifier: Optional[Classifier] = Depends(get_classifier), uid: int = Depends(current_user_id)):
    """Categorize pending leaf transactions that have no category yet (memory, then AI)."""
    return categorize_pending(get_engine(), uid, classifier)

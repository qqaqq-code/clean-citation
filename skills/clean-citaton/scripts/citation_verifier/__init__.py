"""Self-contained citation verification against scholarly metadata APIs."""

from .models import Author, PaperHint, PaperRecord, VerificationResult

__all__ = ["Author", "PaperHint", "PaperRecord", "VerificationResult"]
__version__ = "1.0.0"

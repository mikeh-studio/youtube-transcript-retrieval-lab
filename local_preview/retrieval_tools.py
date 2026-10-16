"""Compatibility alias for retrieval_lab.retrieval_tools; implementation lives there."""

import sys
from pathlib import Path

# Preserve callers that historically added only local_preview to sys.path.
if str(Path(__file__).resolve().parent.parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from retrieval_lab import retrieval_tools as _implementation

sys.modules[__name__] = _implementation

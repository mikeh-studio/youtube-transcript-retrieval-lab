# Headless transcript retrieval tools

`retrieval.transcript_tools` can be imported without starting or importing the
local preview API. It provides a thin interface to an existing retrieval
function and to stored raw transcript segments; it does not initialize a
library, index, search model, or server.

```python
from retrieval.transcript_tools import TranscriptRetrievalTools

# Supply the application's existing library and retrieval callable.
tools = TranscriptRetrievalTools(library=library, retrieve_fn=retrieve)
semantic = tools.semantic_search("search terms", k=5)
keyword = tools.keyword_search("exact terms", k=5)
context = tools.read_context("video-id", timestamp=120, window=30)
```

The retrieval callable receives the query and search options, including
`retrieval_mode="dense"` or `"lexical"`; additional keyword options pass
through. It is responsible for implementing those searches and returning the
result. The library must expose a `videos` mapping. For `read_context`, the
selected video must contain `full_transcript.segments` with timestamped `text`,
`start`, and `end` values. The window is a radius in seconds, and returned
`start` and `end` cover complete selected segments. Missing raw transcripts
cannot be reconstructed from derived chunks by this interface.

Existing imports from `local_preview.retrieval_tools` remain available for
compatibility. This module does not ingest videos or acquire transcripts.

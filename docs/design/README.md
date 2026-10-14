# Design mocks

Static mocks used to agree page designs before building them. They use sample
data and are not wired to the API.

## Search workbench

[Mock page](search-workbench.html) · [screenshot](search-workbench.png)

Search becomes the center of the lab. Decisions agreed for the redesign:

- **A/B by default.** Search compares a baseline run and a candidate run side by
  side, with rank changes and per-query Precision@K and nDCG@K from your labels.
- **Labels in Search.** Relevant / Not relevant on each result writes the labels
  Evaluate uses. Search feedback folds into Evaluate, and the separate Search
  feedback page is removed.
- **Catalog as scope.** Catalog topic and review status narrow the search to
  indexed videos linked to reviewed catalog items. Discovered-only items are
  never searched.
- **Trace before answer.** The routing and agentic tool steps are shown with
  timings; generating an answer is the last step.

**Status:** built. See the [Search workbench](../media/search-workbench.png) and
[Library](../media/library.png) as implemented. Differences from the mock:

- Search runs do not use video routing; routing stays in the Answer flow,
  because it can call an answer provider to expand the query.
- Both runs skip feedback tuning so labels cannot move the rankings.
- The Answer tab reuses the existing answer flow with its own settings.
- Labels are saved per query to the server feedback store. Keeping A/B runs in
  Evaluate's run history and removing the Search feedback page come next.

# Design mocks

Static mocks for planned pages. They use sample data and are not wired to the API.

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

Planned order: Library home and indexing flow, then the Search workbench, then
moving Chunking, Data quality, and Evaluate into the React shell.

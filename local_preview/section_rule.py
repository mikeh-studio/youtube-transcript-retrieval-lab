"""Shared guidance for identifying episode sections in a transcript."""

SECTION_RULE = (
    "Sections are the blocks the show itself treats as one unit, such as a game "
    "discussion, a team preview, a run of listener or Super Chat questions, a fan "
    "question, or an interview topic. Duration does not decide a split: a block may "
    "run 2 minutes or 15. Split inside a block only when the speakers clearly drop "
    "one subject and take up a separate one. Fold pieces under about 90 seconds "
    "(intros, outros, quick tangents) into a neighboring section."
)
NO_SECTION_TARGET = "There is no target number of sections."

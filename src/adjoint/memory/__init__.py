"""Memory pipeline — transcript → daily logs → incremental knowledge base.

Four primary surfaces:
* ``flush`` — distill a single session's transcript into today's daily log.
* ``compile`` — promote daily logs into durable, de-duplicated concept/Q&A articles.
* ``query`` — answer a natural-language question by letting Claude read the KB.
* ``lint`` — seven health checks (broken wikilinks, orphans, stale, sparse, etc.).
"""

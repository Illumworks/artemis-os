# ── recency asked for, not filtered after (2026-09-12) ──────────────────────


def test_the_query_carries_a_recency_window_by_default() -> None:
    """Measured across three literacy queries: unfiltered, Google News RSS returns
    a MEDIAN article age of 361 days and only 10 of 75 inside the 42-day bar the
    signal queue enforces. With `when:42d` it returns 64 of 64 usable, median 19
    days. The scout was doing a full run to have 87% of it rejected downstream —
    and before that bar worked, publishing four-month-old articles as news."""
    from artemis.tools.news import _DEFAULT_RECENCY

    assert _DEFAULT_RECENCY == "42d", "must match MAX_ARTICLE_AGE_DAYS"

    from artemis.marketing.article_recency import MAX_ARTICLE_AGE_DAYS

    assert _DEFAULT_RECENCY == f"{MAX_ARTICLE_AGE_DAYS}d", (
        "the search window and the write-time bar must agree, or one feeds the "
        "other work it will throw away"
    )


def test_an_empty_when_does_not_disable_freshness() -> None:
    """`when: ""` falls back to the default rather than removing the operator.
    Turning freshness off should take a deliberate wide window like '5y', never an
    empty string somebody passed by accident."""
    import inspect

    from artemis.tools import news

    src = inspect.getsource(news._factory)
    assert 'arguments.get("when") or _DEFAULT_RECENCY' in src

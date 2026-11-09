import asyncio

import pytest

from shrink.services.debounce import Debouncer


@pytest.mark.asyncio
async def test_same_sender_burst_only_latest_responds():
    debounce = Debouncer()
    first = asyncio.create_task(debounce.collect(("group", "a"), "先等下", ["image"], "direct",
                                                quiet=.03, maximum=.1))
    await asyncio.sleep(.005)
    second = asyncio.create_task(debounce.collect(("group", "a"), "还有这一条", [], "silent",
                                                 quiet=.03, maximum=.1))
    assert await first is None
    burst = await second
    assert burst.texts == ["先等下", "还有这一条"]
    assert burst.components == ["image"]
    assert burst.reason == "direct"
    assert not debounce.pending


@pytest.mark.asyncio
async def test_other_sender_and_session_are_separate():
    d = Debouncer()
    results = await asyncio.gather(*[
        d.collect(key, str(key), [], "direct", quiet=.005, maximum=.02)
        for key in [("one", "a"), ("one", "b"), ("two", "a")]
    ])
    assert all(len(b.texts) == 1 for b in results)
    assert not d.pending

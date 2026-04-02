"""Optional plugins for everything-is-an-actor.

Redis mailbox (requires ``pip install everything-is-an-actor[redis]``)::

    from everything_is_an_actor.plugins.redis import RedisMailbox

Retry and idempotency helpers::

    from everything_is_an_actor.plugins.retry import ask_with_retry, RetryEnvelope
"""

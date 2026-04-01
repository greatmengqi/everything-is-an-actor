"""Optional plugins for everything-is-an-actor.

Redis mailbox (requires ``pip install everything-is-an-actor[redis]``)::

    from actor_for_agents.plugins.redis import RedisMailbox

Retry and idempotency helpers::

    from actor_for_agents.plugins.retry import ask_with_retry, RetryEnvelope
"""

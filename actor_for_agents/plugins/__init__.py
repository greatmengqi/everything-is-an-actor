"""Optional plugins for actor-for-agents.

Redis mailbox (requires ``pip install actor-for-agents[redis]``)::

    from actor_for_agents.plugins.redis import RedisMailbox

Retry and idempotency helpers::

    from actor_for_agents.plugins.retry import ask_with_retry, RetryEnvelope
"""

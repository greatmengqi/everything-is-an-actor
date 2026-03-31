# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `AgentSystem` — drop-in `ActorSystem` replacement with event-streaming support
- `AgentSystem.run(agent_cls, input)` — spawn root agent and stream all `TaskEvent`s from the actor tree
- `AgentSystem.abort(run_id)` — cancel a running agent tree
- `ActorRef.ask_stream(task)` — stream `TaskEvent`s from an existing ref; symmetric with `ref.ask()`
- `RunStream` — async-iterable `TaskEvent` queue backed by `asyncio.Queue`
- `StreamItem` sealed ADT (`StreamEvent | StreamResult`) for `ask_stream` consumers; supports `match/case`
- `TaskEvent.parent_task_id` — links child events to the calling agent's task (OpenTelemetry-style span)
- `TaskEvent.parent_agent_path` — human-readable parent path for hierarchy visualization
- `ActorContext.dispatch(target, message)` — spawn ephemeral child actor, send one message, await result
- `ActorContext.dispatch_parallel(tasks)` — fan-out to multiple agents concurrently, results in order

## [0.1.0] - 2025-03-31

### Added
- `Actor` base class with `on_receive`, `on_started`, `on_stopped`, `on_restart` lifecycle hooks
- `ActorSystem` — spawn, supervise, and shut down actors
- `ActorRef` — lightweight handle with `tell` (fire-and-forget) and `ask` (request-reply)
- `MemoryMailbox` — high-throughput in-process mailbox (861K msg/s)
- `Middleware` — interceptor chain for all message and lifecycle events
- `OneForOneStrategy` / `AllForOneStrategy` supervision with configurable restart limits
- `plugins.redis.RedisMailbox` — Redis-backed persistent mailbox (optional dep)
- `plugins.retry.ask_with_retry` — bounded retries with exponential backoff + jitter
- `plugins.retry.IdempotentActorMixin` — process-local idempotency for actors

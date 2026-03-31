# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

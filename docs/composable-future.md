# ComposableFuture

Scala 风格的异步组合抽象，基于 Python 原生 `async/await`，零外部依赖。

## 为什么

Python 的 `await` 等价于 Scala 的 `flatMap`，但缺少声明式组合链。当 actor 消息编排超过 3 步时，命令式 try/except/await 散落各处，数据流向难以一眼看清。

```python
# 命令式 — 流向分散在控制结构中
try:
    user = await user_ref.ask(GetUser(id))
    enriched = await enrich_ref.ask(Enrich(user))
    result = enriched.summary
except TimeoutError:
    result = "unknown"

# 声明式 — 一行看清数据流
from everything_is_an_actor.core.composable_future import ComposableFuture as Cf

result = await (
    Cf(user_ref.ask(GetUser(id)))
        .flat_map(lambda u: enrich_ref.ask(Enrich(u)))
        .map(lambda e: e.summary)
        .with_timeout(5.0)
        .recover(lambda _: "unknown")
)
```

## 设计原则

**惰性组合** — 每个 combinator 只创建协程对象（~0.3μs），不执行。整条链在 `await` 时一次性求值。

**零抽象泄漏** — 实现 `__await__` 协议，可以在任何接受 awaitable 的地方使用，包括 `asyncio.gather`、`asyncio.wait_for`。

**跨线程透明** — 通过 `loop` 参数或 `.on(loop)` 绑定目标 event loop，`await` 时自动桥接。阻塞调用方可用 `.result(timeout)`。

## API

### 构造

```python
# 包装任意 awaitable
cf = Cf(ref.ask(msg))

# 绑定到指定 event loop（跨线程场景）
cf = Cf(ref.ask(msg), loop=actor_loop)

# 已知值
cf = Cf.successful(42)
cf = Cf.failed(ValueError("boom"))
```

### 转换

| 方法 | 签名 | 说明 |
|------|------|------|
| `map` | `(T → U) → Cf[U]` | 同步转换结果 |
| `flat_map` | `(T → Awaitable[U]) → Cf[U]` | 异步链式操作 |
| `filter` | `(T → bool) → Cf[T]` | 断言，不满足抛 ValueError |
| `transform` | `(T → U, Exception → U) → Cf[U]` | 同时处理成功和失败 |

### 错误处理

| 方法 | 签名 | 说明 |
|------|------|------|
| `recover` | `(Exception → T) → Cf[T]` | 同步错误恢复 |
| `recover_with` | `(Exception → Awaitable[T]) → Cf[T]` | 异步错误恢复 |
| `fallback_to` | `(() → Awaitable[T]) → Cf[T]` | 失败时执行备选（惰性工厂） |

### 组合

| 方法 | 签名 | 说明 |
|------|------|------|
| `zip` | `(Cf[U]) → Cf[(T, U)]` | 并行执行，返回 tuple |
| `sequence` | `(list[Cf[T]]) → Cf[list[T]]` | 并行执行列表 |
| `first_completed` | `(*Cf[T]) → Cf[T]` | 取最先完成的，默认取消输家 |

### 副作用

| 方法 | 签名 | 说明 |
|------|------|------|
| `and_then` | `(T → Any) → Cf[T]` | 成功时执行副作用，返回原值 |
| `on_complete` | `(ok?, err?) → Cf[T]` | 完成回调，不改变结果 |

### 控制

| 方法 | 签名 | 说明 |
|------|------|------|
| `with_timeout` | `(float) → Cf[T]` | 超时抛 TimeoutError |
| `on` | `(loop) → Cf[T]` | 绑定目标 event loop |
| `result` | `(timeout?) → T` | 阻塞获取（非 async 线程用） |

## 跨线程使用

### 场景 1：async 调用方在不同 loop

```python
# actor 运行在 bg_loop，调用方在 main loop
result = await Cf(ref.ask(msg), loop=bg_loop).map(process)
# 自动 run_coroutine_threadsafe 桥接，map 在 bg_loop 执行
```

### 场景 2：非 async 线程阻塞获取

```python
# 普通线程中
result = Cf(ref.ask(msg), loop=actor_loop).map(f).result(timeout=5.0)
```

### 场景 3：后续绑定 loop

```python
cf = Cf.successful(data).map(transform)
result = await cf.on(target_loop)  # 延迟绑定
```

## 取消传播

取消是双向的：

```
caller 取消 task
    ↓
proxy Future 被取消
    ↓ (backward callback)
source Future 在 source_loop 上被取消

source Future 被取消
    ↓ (forward callback)
proxy Future 在 caller_loop 上被取消
    ↓
caller 收到 CancelledError
```

`first_completed` 默认 `cancel_pending=True`：赢家返回后，输家被取消并等待 settle，防止隐藏副作用。传 `cancel_pending=False` 用于只读/幂等分支。

## 性能

在 Apple M1 上的基准（Python 3.12）：

| 操作 | 每次开销 | ops/sec |
|------|---------|---------|
| 裸 `await` | 0.16μs | 6,370,000 |
| `Cf.successful` | 0.41μs | 2,430,000 |
| Cf + map(1) | 0.81μs | 1,230,000 |
| Cf + map(3 chain) | 1.63μs | 613,000 |
| 跨 loop await | 63μs | 15,900 |
| 跨 loop + 3 chain | 66μs | 15,200 |

同 loop 单层 map 增量 ~0.65μs。跨 loop 瓶颈在线程间唤醒（~63μs），combinator 链长度对跨 loop 开销无显著影响。

## 已知限制

- **链深度上限 ~200** — 每层 combinator 增加 ~2 个协程帧，Python 默认递归限制 1000。实际场景很少超过 10 层。
- **不可重复 await** — 和原生协程一致，`await` 消费后不能再次 `await`。
- **`result()` 不能在 async 上下文中调用** — 会 fail-fast 抛 RuntimeError，提示用 `await`。
- **`fallback_to` 接受工厂函数** — `lambda: ref.ask(backup)` 而非直接传 awaitable，避免未 await 的协程警告。

# Actor Framework Best Practices

> 按生产价值排序，不按性能。

---

## 1. 隔离阻塞代码 ⚠️ 最重要

### 问题
`on_receive()` 里的阻塞代码会卡住整个事件循环，影响所有 actor。

### 方案

```python
# ✅ receive() 自动在线程池跑，不影响其他 actor
class FileActor(Actor):
    def receive(self, message):
        return open(message).read()  # 线程池处理

# ❌ on_receive 里写阻塞代码会卡事件循环
class BadActor(Actor):
    async def on_receive(self, message):
        time.sleep(10)  # ❌ 阻塞整个系统！
```

### 压测

| Mailbox | Actor | tell 50K | 说明 |
|---------|-------|----------|------|
| MemoryMailbox | `receive()` | 2.0M/s | 有阻塞代码首选 |

### 适合
- 有同步阻塞 I/O（文件、网络）
- CPU 密集计算
- 调用第三方库没有 async 版本

### 不适合
- 需要 await 的场景（用 `on_receive()`）

---

## 2. 正确处理错误

### 问题
吞掉错误会让 bug 难以发现，系统进入未知状态。

### 方案

```python
# ✅ 让错误传播，supervisor 会处理
class RobustActor(Actor):
    async def on_receive(self, message):
        return risky_operation()  # 异常会触发 restart

# ❌ 吞掉错误，问题被隐藏
class SilentFailActor(Actor):
    async def on_receive(self, message):
        try:
            return risky_operation()
        except:
            return None  # ❌ 错误消失了！
```

### 适合
所有 actor

### 不适合
无

---

## 3. 资源清理

### 问题
临时 actor 不停止会泄漏内存。

### 方案

```python
# ✅ 显式停止
ref = await system.spawn(TempActor, "temp")
await ref.ask(data)
ref.stop()
await ref.join()

# ✅ 自动停止
class OneTimeInit(Actor):
    def stop_policy(self):
        return StopMode.ONE_TIME

    def receive(self, message):
        return initialize(message)
```

### 适合
临时 actor、一次性任务

### 不适合
常驻 actor

---

## 4. 调试友好

### 问题
随机名字难追踪。

### 方案

```python
# ✅ 有意义的名字
await system.spawn(DataProcessor, "processor-1")
await system.spawn(DataProcessor, "processor-2")

# ❌ 随机名字
await system.spawn(DataProcessor, f"actor-{uuid4()}")
```

### 适合
所有 actor

### 不适合
无

---

## 5. 避免共享状态

### 问题
多 actor 并发访问共享状态会产生奇怪 bug。

### 方案

```python
# ✅ 每个 actor 独立状态
class CounterActor(Actor):
    def __init__(self):
        self.count = 0

    def receive(self, message):
        self.count += 1
        return self.count

# ❌ 全局共享状态
counter = 0
class BadActor(Actor):
    def receive(self, message):
        global counter
        counter += 1  # ❌ 并发问题
```

### 适合
所有 actor

### 不适合
无

---

## 6. 性能选择（最后才考虑）

### Case 1: 高吞吐纯计算

```python
system = ActorSystem('app', mailbox_cls=FastMailbox)

class ComputeActor(Actor):
    def receive(self, message):
        return heavy_computation(message)
```

| Mailbox | Actor | tell 50K | ask 10K |
|---------|-------|----------|----------|
| FastMailbox | `receive()` | **2.5M/s** | 14.3K/s |

**适合**: 高吞吐、无阻塞、纯计算
**不适合**: 有 I/O、LLM 调用

---

### Case 2: 有 async I/O（最常见）

```python
system = ActorSystem('app', mailbox_cls=FastMailbox)

class HttpActor(Actor):
    async def on_receive(self, message):
        return await http.get(message)
```

| Mailbox | Actor | tell 50K | ask 50K |
|---------|-------|----------|----------|
| FastMailbox | `on_receive()` | 2.6M/s | **28.9K/s** |

**适合**: HTTP、数据库、async SDK（90%场景）
**不适合**: 同步阻塞代码

---

### Case 3: 有阻塞代码

```python
system = ActorSystem('app')  # 默认 MemoryMailbox

class FileActor(Actor):
    def receive(self, message):
        return blocking_io()  # 自动线程池隔离
```

| Mailbox | Actor | tell 50K | ask 50K |
|---------|-------|----------|----------|
| MemoryMailbox | `receive()` | 1.7M/s | 12.9K/s |

**适合**: 同步阻塞 I/O、文件、网络
**不适合**: 需要 await 的场景

---

### Case 4: CPU 多核并行

```python
system = ActorSystem('app', mailbox_cls=ThreadedMailbox)

class CPUActor(Actor):
    def receive(self, message):
        return cpu_work(message)
```

| Mailbox | Actor | tell 50K | ask 50K |
|---------|-------|----------|----------|
| ThreadedMailbox | `receive()` | 1.1M/s | 10.3K/s |

**适合**: CPU 密集、多核并行
**不适合**: 大部分场景，多线程开销大

---

### Case 5: 默认配置（推荐新手）

```python
system = ActorSystem('app')  # MemoryMailbox + receive() 默认

class DefaultActor(Actor):
    def receive(self, message):  # 用 receive() 而非 on_receive()
        return processing(message)  # 自动线程池处理
```

| Mailbox | Actor | tell 50K | ask 50K |
|---------|-------|----------|----------|
| MemoryMailbox | `receive()` | 1.7M/s | 12.9K/s |

**推荐作为默认选择**：`receive()` 即使写阻塞代码也不影响其他 actor，比 `on_receive()` 更安全。

---

## 总结

| 场景 | 推荐组合 | tell 50K | ask 50K |
|------|---------|----------|----------|
| 有 async I/O | FastMailbox + `on_receive()` | 2.6M/s | **28.9K/s** |
| 高吞吐纯计算 | FastMailbox + `receive()` | **2.5M/s** | 14.3K/s |
| **默认（新手推荐）** | MemoryMailbox + `receive()` | 1.7M/s | 12.9K/s |
| CPU 多核并行 | ThreadedMailbox + `receive()` | 1.1M/s | 10.3K/s |

**测试环境**: macOS, 500K tell / 50K ask

**新手建议**：先用 `receive()`，有 async I/O 需求再换 `on_receive()`。

**记住**：写出问题比写快重要 100 倍。

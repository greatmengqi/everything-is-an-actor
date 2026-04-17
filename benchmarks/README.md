# Actor System 压测工具

完整的性能测试套件，用于测试和监控 Actor System 的性能表现。

## 快速开始

```bash
# 运行所有基础测试（P0 + P1）
python benchmarks/run_all_tests.py

# 只运行基础性能测试
python benchmarks/run_all_tests.py --basic

# 只运行系统模式对比测试
python benchmarks/run_all_tests.py --modes

# 只运行 Agent Layer 测试
python benchmarks/run_all_tests.py --agent

# 运行所有测试（包括极限压力测试）
python benchmarks/run_all_tests.py --all
```

## 测试分类

### P0 - 基础性能测试（必跑）

**文件**: `test_basic_performance.py`

测试场景：
- 单 Actor tell/ask 吞吐量
- 不同邮箱实现对比（MemoryMailbox, FastMailbox, ThreadedMailbox）
- 同步 vs 异步 Actor 性能对比
- 多 Actor 并发性能

运行：
```bash
python benchmarks/test_basic_performance.py
```

### P1 - 系统模式对比测试

**文件**: `test_system_modes.py`

测试场景：
- Single Loop vs Multi-Loop 性能对比
- 阻塞操作隔离效果
- 跨 Loop 通信性能

运行：
```bash
python benchmarks/test_system_modes.py
```

### P1 - Agent Layer 性能测试

**文件**: `test_agent_layer.py`

测试场景：
- AgentActor 任务处理性能
- 并发原语性能（sequence, traverse 等）
- 事件流性能
- 流式输出性能

运行：
```bash
python benchmarks/test_agent_layer.py
```

### P2 - 极限压力测试（可选）

**文件**: `test_stress.py`

测试场景：
- 邮箱满载测试（不同背压策略）
- 长时间稳定性测试
- 故障恢复测试

运行：
```bash
python benchmarks/test_stress.py
```

## 性能回归检测

### 创建基准

首次运行后，创建性能基准：

```bash
# 从测试结果创建基准
python -c "
from benchmarks.regression_check import create_baseline_from_results
create_baseline_from_results('benchmarks/results/all_results.json')
"
```

### 检测回归

```bash
# 运行回归检测
python benchmarks/regression_check.py
```

回归检测会对比当前性能与基准性能，检测：
- 吞吐量下降超过 20%
- 延迟增加超过 50%
- 内存使用增加超过 50%

## 输出文件

所有测试结果保存在 `benchmarks/results/` 目录：

```
benchmarks/results/
├── basic_performance_results.json    # 基础性能测试结果
├── basic_performance_report.md       # 基础性能测试报告
├── system_mode_results.json          # 系统模式测试结果
├── system_mode_report.md             # 系统模式测试报告
├── agent_layer_results.json          # Agent Layer 测试结果
├── agent_layer_report.md             # Agent Layer 测试报告
├── stress_test_results.json          # 极限压力测试结果
├── stress_test_report.md             # 极限压力测试报告
├── all_results.json                  # 所有测试合并结果
└── summary_report.md                 # 汇总报告
```

## 性能指标

每个测试场景收集以下指标：

| 指标 | 说明 |
|------|------|
| `msg_per_sec` | 吞吐量（消息/秒） |
| `avg_latency_ms` | 平均延迟（毫秒） |
| `median_latency_ms` | 中位延迟（毫秒） |
| `p99_latency_ms` | P99 延迟（毫秒） |
| `p999_latency_ms` | P99.9 延迟（毫秒） |
| `peak_memory_mb` | 内存峰值（MB） |
| `avg_cpu_percent` | 平均 CPU 使用率 |
| `error_count` | 错误数 |
| `timeout_count` | 超时数 |

## 性能基准

| 场景 | 基准吞吐量 | 最低要求 |
|------|-----------|----------|
| 单 Actor tell (FastMailbox) | 150K msg/s | 100K msg/s |
| 单 Actor ask (FastMailbox) | 50K msg/s | 30K msg/s |
| 100 Actor 并发 | 120K msg/s | 80K msg/s |
| 跨 Loop 通信 (4 Loops) | 60K msg/s | 40K msg/s |
| Agent ask | 30K msg/s | 20K msg/s |

## 监控工具

### SystemMonitor

实时监控系统资源：

```python
from benchmarks.monitor import SystemMonitor

monitor = SystemMonitor(interval=0.1)
await monitor.start()

# ... 运行测试 ...

monitor.stop()
stats = monitor.get_stats()
# {'peak_memory_mb': 256, 'avg_cpu_percent': 45.2, 'peak_fds': 128}
```

### LatencyTracker

跟踪延迟分布：

```python
from benchmarks.monitor import LatencyTracker

tracker = LatencyTracker()

start = time.perf_counter()
# ... 操作 ...
tracker.record(start)

stats = tracker.get_stats()
# {'avg_ms': 1.2, 'p99_ms': 5.3, ...}
```

## CI/CD 集成

### GitHub Actions

```yaml
name: Performance Benchmark

on:
  pull_request:
    branches: [main]

jobs:
  benchmark:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - name: Setup Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      - name: Install dependencies
        run: |
          pip install -e .
          pip install psutil
      - name: Run benchmarks
        run: python benchmarks/run_all_tests.py
      - name: Check regression
        run: python benchmarks/regression_check.py
```

## 扩展测试

### 添加新测试场景

1. 在 `test_*.py` 中添加测试函数：

```python
async def scenario_new_test(
    warmup: bool = False,
    **kwargs
) -> Tuple[int, List[float], Dict[str, Any]]:
    """新测试场景"""
    # 实现测试逻辑
    return total_messages, latencies, stats
```

2. 在 `run_*_tests()` 函数中调用：

```python
await runner.run_scenario(
    "new_test",
    scenario_new_test,
    param1=value1,
)
```

### 自定义监控

```python
from benchmarks.stress_test_framework import StressTestRunner

runner = StressTestRunner(output_dir="custom_results")

result = await runner.run_scenario(
    "custom_test",
    my_test_func,
    custom_param=value,
)

print(result)
```

## 故障排查

### 问题：测试超时

**原因**: 消息处理过慢或死锁

**解决**:
- 检查 Actor 实现是否有阻塞操作
- 增加 timeout 参数
- 使用 Multi-Loop 模式隔离阻塞操作

### 问题：内存持续增长

**原因**: 可能存在内存泄漏

**解决**:
- 运行长时间稳定性测试
- 检查 Actor 是否正确清理资源
- 使用内存分析工具（如 memory_profiler）

### 问题：吞吐量异常低

**原因**: 可能存在性能瓶颈

**解决**:
- 检查是否有不必要的序列化
- 确认使用了正确的邮箱类型
- 对比基准性能找出差异

## 最佳实践

1. **定期运行**: 每次重要改动后运行基础测试
2. **保存基准**: 定期更新性能基准
3. **监控趋势**: 关注性能变化趋势
4. **分类测试**: 根据场景选择合适的测试集
5. **资源监控**: 始终监控内存和 CPU 使用

## 参考资料

- [压测方案设计文档](../docs/stress-test-plan.md)
- [Actor System 架构设计](../docs/unified-actor-system-design.md)
- [Agent Layer 文档](../docs/AGENTS.md)

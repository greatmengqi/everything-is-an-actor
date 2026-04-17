"""
压测框架 - 提供压测运行、结果收集和分析功能
"""

from __future__ import annotations

import asyncio
import json
import statistics
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


@dataclass
class StressTestResult:
    """压测结果"""
    test_name: str
    total_messages: int
    duration_sec: float
    msg_per_sec: float
    
    # 延迟统计
    avg_latency_ms: float = 0.0
    median_latency_ms: float = 0.0
    p99_latency_ms: float = 0.0
    p999_latency_ms: float = 0.0
    
    # 资源使用
    peak_memory_mb: float = 0.0
    avg_cpu_percent: float = 0.0
    
    # 错误统计
    error_count: int = 0
    timeout_count: int = 0
    
    # 配置信息
    config: Dict[str, Any] = field(default_factory=dict)
    
    # 时间戳
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return asdict(self)
    
    def __str__(self) -> str:
        """格式化输出"""
        return (
            f"{self.test_name}:\n"
            f"  吞吐量: {self.msg_per_sec:,.0f} msg/s\n"
            f"  平均延迟: {self.avg_latency_ms:.2f} ms\n"
            f"  P99 延迟: {self.p99_latency_ms:.2f} ms\n"
            f"  内存峰值: {self.peak_memory_mb:.1f} MB\n"
            f"  错误数: {self.error_count}\n"
        )


class StressTestRunner:
    """压测运行器"""
    
    def __init__(self, output_dir: str = "benchmarks/results"):
        self.results: List[StressTestResult] = []
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
    
    async def run_scenario(
        self,
        name: str,
        scenario_func: Callable,
        warmup: bool = True,
        **kwargs
    ) -> StressTestResult:
        """
        运行单个压测场景
        
        Args:
            name: 测试名称
            scenario_func: 测试函数，返回 (total_messages, latencies, stats)
            warmup: 是否预热
            **kwargs: 传递给测试函数的参数
        """
        print(f"\n{'='*60}")
        print(f"运行测试: {name}")
        print(f"{'='*60}")
        
        # 预热
        if warmup:
            print("预热中...")
            try:
                await scenario_func(warmup=True, **kwargs)
            except Exception as e:
                print(f"预热失败: {e}")
        
        # 正式测试
        print("开始测试...")
        start_time = time.perf_counter()
        
        try:
            total_messages, latencies, stats = await scenario_func(**kwargs)
        except Exception as e:
            print(f"测试失败: {e}")
            import traceback
            traceback.print_exc()
            total_messages = 0
            latencies = []
            stats = {}
        
        duration = time.perf_counter() - start_time
        
        # 统计分析
        result = StressTestResult(
            test_name=name,
            total_messages=total_messages,
            duration_sec=duration,
            msg_per_sec=total_messages / duration if duration > 0 else 0,
            config=kwargs,
        )
        
        # 延迟统计
        if latencies:
            result.avg_latency_ms = statistics.mean(latencies) * 1000
            result.median_latency_ms = statistics.median(latencies) * 1000
            result.p99_latency_ms = self._percentile(latencies, 99) * 1000
            result.p999_latency_ms = self._percentile(latencies, 99.9) * 1000
        
        # 资源统计
        result.peak_memory_mb = stats.get('peak_memory_mb', 0)
        result.avg_cpu_percent = stats.get('avg_cpu_percent', 0)
        result.error_count = stats.get('error_count', 0)
        result.timeout_count = stats.get('timeout_count', 0)
        
        self.results.append(result)
        
        # 打印结果
        print(f"\n结果:")
        print(f"  总消息数: {total_messages:,}")
        print(f"  耗时: {duration:.2f}s")
        print(f"  吞吐量: {result.msg_per_sec:,.0f} msg/s")
        if latencies:
            print(f"  平均延迟: {result.avg_latency_ms:.2f}ms")
            print(f"  P99延迟: {result.p99_latency_ms:.2f}ms")
        print(f"  内存峰值: {result.peak_memory_mb:.1f}MB")
        
        return result
    
    def _percentile(self, data: List[float], percentile: float) -> float:
        """计算百分位数"""
        if not data:
            return 0.0
        sorted_data = sorted(data)
        index = int(len(sorted_data) * percentile / 100)
        return sorted_data[min(index, len(sorted_data) - 1)]
    
    def save_results(self, filename: str = "stress_test_results.json"):
        """保存结果到文件"""
        output_file = self.output_dir / filename
        data = {
            "timestamp": datetime.now().isoformat(),
            "results": [r.to_dict() for r in self.results],
        }
        
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        
        print(f"\n结果已保存到: {output_file}")
    
    def generate_report(self) -> str:
        """生成 Markdown 格式的报告"""
        report = []
        report.append("# Actor System 压测报告\n")
        report.append(f"**测试时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        report.append(f"**测试场景数**: {len(self.results)}\n")
        
        # 汇总表格
        report.append("\n## 性能摘要\n")
        report.append("| 测试场景 | 吞吐量 (msg/s) | 平均延迟 (ms) | P99延迟 (ms) | 内存峰值 (MB) |\n")
        report.append("|----------|----------------|---------------|--------------|---------------|\n")
        
        for r in self.results:
            report.append(
                f"| {r.test_name} | {r.msg_per_sec:,.0f} | "
                f"{r.avg_latency_ms:.2f} | {r.p99_latency_ms:.2f} | "
                f"{r.peak_memory_mb:.1f} |\n"
            )
        
        # 详细结果
        report.append("\n## 详细结果\n")
        for r in self.results:
            report.append(f"\n### {r.test_name}\n")
            report.append(f"- 总消息数: {r.total_messages:,}\n")
            report.append(f"- 耗时: {r.duration_sec:.2f}s\n")
            report.append(f"- 吞吐量: {r.msg_per_sec:,.0f} msg/s\n")
            if r.avg_latency_ms > 0:
                report.append(f"- 平均延迟: {r.avg_latency_ms:.2f}ms\n")
                report.append(f"- 中位延迟: {r.median_latency_ms:.2f}ms\n")
                report.append(f"- P99延迟: {r.p99_latency_ms:.2f}ms\n")
                report.append(f"- P99.9延迟: {r.p999_latency_ms:.2f}ms\n")
            report.append(f"- 内存峰值: {r.peak_memory_mb:.1f}MB\n")
            if r.error_count > 0:
                report.append(f"- 错误数: {r.error_count}\n")
        
        return ''.join(report)
    
    def print_summary(self):
        """打印汇总信息"""
        print("\n" + "="*60)
        print("压测汇总")
        print("="*60)
        
        for r in self.results:
            print(f"\n{r}")

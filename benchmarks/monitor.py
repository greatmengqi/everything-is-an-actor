"""
系统资源监控工具
"""

import asyncio
import os
import time
from typing import List, Dict, Any


class SystemMonitor:
    """系统资源监控器"""
    
    def __init__(self, interval: float = 0.1):
        """
        Args:
            interval: 采样间隔（秒）
        """
        self.interval = interval
        self.running = False
        self.samples: List[Dict[str, Any]] = []
        self._task = None
    
    async def start(self):
        """开始监控"""
        self.running = True
        self.samples = []
        self._task = asyncio.create_task(self._monitor_loop())
    
    def stop(self):
        """停止监控"""
        self.running = False
        if self._task:
            self._task.cancel()
    
    async def _monitor_loop(self):
        """监控循环"""
        try:
            # 尝试导入 psutil
            try:
                import psutil
                process = psutil.Process()
                use_psutil = True
            except ImportError:
                use_psutil = False
                print("警告: 未安装 psutil，将使用基础监控")
            
            while self.running:
                sample = {
                    'timestamp': time.time(),
                }
                
                if use_psutil:
                    sample['cpu_percent'] = process.cpu_percent(interval=None)
                    sample['memory_mb'] = process.memory_info().rss / 1024 / 1024
                    sample['fds'] = self._get_fd_count()
                else:
                    # 基础监控（仅内存）
                    import resource
                    sample['memory_mb'] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
                    sample['cpu_percent'] = 0
                    sample['fds'] = 0
                
                self.samples.append(sample)
                await asyncio.sleep(self.interval)
        
        except asyncio.CancelledError:
            pass
    
    def _get_fd_count(self) -> int:
        """获取文件描述符数量"""
        try:
            import psutil
            return psutil.Process().num_fds() if hasattr(psutil.Process(), 'num_fds') else 0
        except:
            return 0
    
    def get_stats(self) -> Dict[str, float]:
        """获取统计信息"""
        if not self.samples:
            return {
                'peak_memory_mb': 0,
                'avg_cpu_percent': 0,
                'peak_fds': 0,
            }
        
        return {
            'peak_memory_mb': max(s['memory_mb'] for s in self.samples),
            'avg_cpu_percent': sum(s['cpu_percent'] for s in self.samples) / len(self.samples),
            'peak_fds': max(s['fds'] for s in self.samples),
        }


class LatencyTracker:
    """延迟跟踪器"""
    
    def __init__(self):
        self.latencies: List[float] = []
    
    def record(self, start_time: float, end_time: float = None):
        """记录延迟"""
        if end_time is None:
            end_time = time.perf_counter()
        self.latencies.append(end_time - start_time)
    
    def get_stats(self) -> Dict[str, float]:
        """获取统计信息"""
        if not self.latencies:
            return {}
        
        import statistics
        sorted_latencies = sorted(self.latencies)
        
        def percentile(p):
            index = int(len(sorted_latencies) * p / 100)
            return sorted_latencies[min(index, len(sorted_latencies) - 1)]
        
        return {
            'count': len(self.latencies),
            'avg_ms': statistics.mean(self.latencies) * 1000,
            'median_ms': statistics.median(self.latencies) * 1000,
            'min_ms': min(self.latencies) * 1000,
            'max_ms': max(self.latencies) * 1000,
            'p99_ms': percentile(99) * 1000,
            'p999_ms': percentile(99.9) * 1000,
        }


class ThroughputCounter:
    """吞吐量计数器"""
    
    def __init__(self):
        self.count = 0
        self.start_time = None
    
    def start(self):
        """开始计数"""
        self.start_time = time.perf_counter()
    
    def increment(self, n: int = 1):
        """增加计数"""
        self.count += n
    
    def get_rate(self) -> float:
        """获取速率"""
        if self.start_time is None:
            return 0
        elapsed = time.perf_counter() - self.start_time
        return self.count / elapsed if elapsed > 0 else 0

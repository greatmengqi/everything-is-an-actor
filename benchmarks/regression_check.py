"""
性能回归检测工具

对比当前性能与基准性能，检测性能回退
"""

import json
from pathlib import Path
from typing import Dict, List, Any, Optional
from dataclasses import dataclass


@dataclass
class RegressionCheckResult:
    """回归检测结果"""
    test_name: str
    status: str  # 'pass', 'regression', 'improvement', 'new_test'
    metric: Optional[str] = None
    baseline_value: Optional[float] = None
    current_value: Optional[float] = None
    change_percent: Optional[float] = None
    message: str = ""


class RegressionChecker:
    """性能回归检测器"""
    
    # 性能阈值配置
    THROUGHPUT_REGRESSION_THRESHOLD = -20.0  # 吞吐量下降超过 20% 视为回归
    LATENCY_REGRESSION_THRESHOLD = 50.0      # 延迟增加超过 50% 视为回归
    MEMORY_REGRESSION_THRESHOLD = 50.0       # 内存增加超过 50% 视为回归
    
    def __init__(self, baseline_file: str = "benchmarks/baseline.json"):
        """
        Args:
            baseline_file: 基准性能文件路径
        """
        self.baseline_file = Path(baseline_file)
        self.baseline = self._load_baseline()
    
    def _load_baseline(self) -> Dict[str, Any]:
        """加载基准性能数据"""
        if not self.baseline_file.exists():
            print(f"警告: 基准文件不存在 {self.baseline_file}")
            return {}
        
        with open(self.baseline_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # 转换为以 test_name 为 key 的字典
        baseline = {}
        for result in data.get('results', []):
            test_name = result['test_name']
            baseline[test_name] = result
        
        return baseline
    
    def check(self, current_result: Dict[str, Any]) -> RegressionCheckResult:
        """
        检查单个测试结果是否回归
        
        Args:
            current_result: 当前测试结果
        """
        test_name = current_result['test_name']
        
        # 新测试
        if test_name not in self.baseline:
            return RegressionCheckResult(
                test_name=test_name,
                status='new_test',
                message=f"新测试: {test_name}",
            )
        
        baseline = self.baseline[test_name]
        
        # 检查吞吐量
        throughput_result = self._check_throughput(baseline, current_result)
        if throughput_result.status == 'regression':
            return throughput_result
        
        # 检查延迟
        latency_result = self._check_latency(baseline, current_result)
        if latency_result.status == 'regression':
            return latency_result
        
        # 检查内存
        memory_result = self._check_memory(baseline, current_result)
        if memory_result.status == 'regression':
            return memory_result
        
        # 如果有改进，返回改进信息
        if throughput_result.status == 'improvement':
            return throughput_result
        
        # 通过
        return RegressionCheckResult(
            test_name=test_name,
            status='pass',
            message="性能正常",
        )
    
    def _check_throughput(
        self,
        baseline: Dict[str, Any],
        current: Dict[str, Any]
    ) -> RegressionCheckResult:
        """检查吞吐量"""
        baseline_throughput = baseline.get('msg_per_sec', 0)
        current_throughput = current.get('msg_per_sec', 0)
        
        if baseline_throughput == 0:
            return RegressionCheckResult(
                test_name=current['test_name'],
                status='pass',
                message="无基准吞吐量数据",
            )
        
        change_percent = (current_throughput - baseline_throughput) / baseline_throughput * 100
        
        if change_percent < self.THROUGHPUT_REGRESSION_THRESHOLD:
            return RegressionCheckResult(
                test_name=current['test_name'],
                status='regression',
                metric='throughput',
                baseline_value=baseline_throughput,
                current_value=current_throughput,
                change_percent=change_percent,
                message=f"吞吐量回归: {baseline_throughput:.0f} → {current_throughput:.0f} msg/s ({change_percent:+.1f}%)",
            )
        
        if change_percent > 10:  # 改进超过 10%
            return RegressionCheckResult(
                test_name=current['test_name'],
                status='improvement',
                metric='throughput',
                baseline_value=baseline_throughput,
                current_value=current_throughput,
                change_percent=change_percent,
                message=f"吞吐量提升: {baseline_throughput:.0f} → {current_throughput:.0f} msg/s ({change_percent:+.1f}%)",
            )
        
        return RegressionCheckResult(
            test_name=current['test_name'],
            status='pass',
            metric='throughput',
            change_percent=change_percent,
        )
    
    def _check_latency(
        self,
        baseline: Dict[str, Any],
        current: Dict[str, Any]
    ) -> RegressionCheckResult:
        """检查延迟"""
        baseline_latency = baseline.get('avg_latency_ms', 0)
        current_latency = current.get('avg_latency_ms', 0)
        
        if baseline_latency == 0:
            return RegressionCheckResult(
                test_name=current['test_name'],
                status='pass',
                message="无基准延迟数据",
            )
        
        change_percent = (current_latency - baseline_latency) / baseline_latency * 100
        
        if change_percent > self.LATENCY_REGRESSION_THRESHOLD:
            return RegressionCheckResult(
                test_name=current['test_name'],
                status='regression',
                metric='latency',
                baseline_value=baseline_latency,
                current_value=current_latency,
                change_percent=change_percent,
                message=f"延迟回归: {baseline_latency:.2f} → {current_latency:.2f} ms ({change_percent:+.1f}%)",
            )
        
        return RegressionCheckResult(
            test_name=current['test_name'],
            status='pass',
            metric='latency',
            change_percent=change_percent,
        )
    
    def _check_memory(
        self,
        baseline: Dict[str, Any],
        current: Dict[str, Any]
    ) -> RegressionCheckResult:
        """检查内存使用"""
        baseline_memory = baseline.get('peak_memory_mb', 0)
        current_memory = current.get('peak_memory_mb', 0)
        
        if baseline_memory == 0:
            return RegressionCheckResult(
                test_name=current['test_name'],
                status='pass',
                message="无基准内存数据",
            )
        
        change_percent = (current_memory - baseline_memory) / baseline_memory * 100
        
        if change_percent > self.MEMORY_REGRESSION_THRESHOLD:
            return RegressionCheckResult(
                test_name=current['test_name'],
                status='regression',
                metric='memory',
                baseline_value=baseline_memory,
                current_value=current_memory,
                change_percent=change_percent,
                message=f"内存回归: {baseline_memory:.1f} → {current_memory:.1f} MB ({change_percent:+.1f}%)",
            )
        
        return RegressionCheckResult(
            test_name=current['test_name'],
            status='pass',
            metric='memory',
            change_percent=change_percent,
        )
    
    def check_all(
        self,
        current_results: List[Dict[str, Any]]
    ) -> List[RegressionCheckResult]:
        """
        检查所有测试结果
        
        Args:
            current_results: 当前测试结果列表
        """
        results = []
        
        for result in current_results:
            check_result = self.check(result)
            results.append(check_result)
        
        return results
    
    def generate_report(
        self,
        check_results: List[RegressionCheckResult]
    ) -> str:
        """生成回归检测报告"""
        report = []
        report.append("# 性能回归检测报告\n")
        
        # 统计
        total = len(check_results)
        passed = sum(1 for r in check_results if r.status == 'pass')
        regressions = sum(1 for r in check_results if r.status == 'regression')
        improvements = sum(1 for r in check_results if r.status == 'improvement')
        new_tests = sum(1 for r in check_results if r.status == 'new_test')
        
        report.append(f"**总计**: {total} 个测试\n")
        report.append(f"**通过**: {passed}\n")
        report.append(f"**回归**: {regressions}\n")
        report.append(f"**改进**: {improvements}\n")
        report.append(f"**新测试**: {new_tests}\n")
        
        # 回归详情
        if regressions > 0:
            report.append("\n## ⚠️ 性能回归\n")
            for r in check_results:
                if r.status == 'regression':
                    report.append(f"\n### {r.test_name}\n")
                    report.append(f"- **指标**: {r.metric}\n")
                    report.append(f"- **基准值**: {r.baseline_value:.2f}\n")
                    report.append(f"- **当前值**: {r.current_value:.2f}\n")
                    report.append(f"- **变化**: {r.change_percent:+.1f}%\n")
                    report.append(f"- **说明**: {r.message}\n")
        
        # 改进详情
        if improvements > 0:
            report.append("\n## ✅ 性能改进\n")
            for r in check_results:
                if r.status == 'improvement':
                    report.append(f"- {r.test_name}: {r.message}\n")
        
        # 新测试
        if new_tests > 0:
            report.append("\n## 🆕 新测试\n")
            for r in check_results:
                if r.status == 'new_test':
                    report.append(f"- {r.test_name}\n")
        
        return ''.join(report)


def create_baseline_from_results(
    results_file: str,
    output_file: str = "benchmarks/baseline.json"
):
    """
    从测试结果创建基准文件
    
    Args:
        results_file: 测试结果文件
        output_file: 输出基准文件
    """
    with open(results_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    # 复制到基准文件
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    
    print(f"基准文件已创建: {output_file}")


async def run_regression_check(results_file: str = "benchmarks/results/stress_test_results.json"):
    """运行回归检测"""
    # 加载当前结果
    with open(results_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    current_results = data.get('results', [])
    
    # 执行回归检测
    checker = RegressionChecker()
    check_results = checker.check_all(current_results)
    
    # 生成报告
    report = checker.generate_report(check_results)
    print(report)
    
    # 保存报告
    report_file = Path("benchmarks/results/regression_report.md")
    with open(report_file, 'w', encoding='utf-8') as f:
        f.write(report)
    
    print(f"\n回归检测报告已保存到: {report_file}")
    
    # 返回是否有回归
    has_regression = any(r.status == 'regression' for r in check_results)
    return not has_regression


if __name__ == "__main__":
    import asyncio
    
    # 示例：创建基准
    # create_baseline_from_results("benchmarks/results/basic_performance_results.json")
    
    # 示例：运行回归检测
    asyncio.run(run_regression_check())

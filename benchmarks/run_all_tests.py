#!/usr/bin/env python3
"""
统一压测运行脚本

运行所有压测场景并生成完整报告
"""

import asyncio
import sys
from pathlib import Path
from datetime import datetime

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))


async def run_all_benchmarks(
    run_basic: bool = True,
    run_modes: bool = True,
    run_agent: bool = True,
    run_stress: bool = False,  # 极限压力测试默认不运行
):
    """
    运行所有压测
    
    Args:
        run_basic: 是否运行基础性能测试
        run_modes: 是否运行系统模式对比测试
        run_agent: 是否运行 Agent Layer 测试
        run_stress: 是否运行极限压力测试
    """
    from benchmarks.stress_test_framework import StressTestRunner
    
    print("\n" + "="*60)
    print("Actor System 完整压测套件")
    print("="*60)
    print(f"开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    
    # 导入测试模块
    if run_basic:
        from benchmarks.test_basic_performance import run_basic_performance_tests
    if run_modes:
        from benchmarks.test_system_modes import run_system_mode_tests
    if run_agent:
        from benchmarks.test_agent_layer import run_agent_layer_tests
    if run_stress:
        from benchmarks.test_stress import run_stress_tests
    
    # 运行测试
    if run_basic:
        print("\n" + "="*60)
        print("阶段 1: 基础性能测试（P0）")
        print("="*60)
        await run_basic_performance_tests()
    
    if run_modes:
        print("\n" + "="*60)
        print("阶段 2: 系统模式对比测试（P1）")
        print("="*60)
        await run_system_mode_tests()
    
    if run_agent:
        print("\n" + "="*60)
        print("阶段 3: Agent Layer 性能测试（P1）")
        print("="*60)
        await run_agent_layer_tests()
    
    if run_stress:
        print("\n" + "="*60)
        print("阶段 4: 极限压力测试（P2）")
        print("="*60)
        await run_stress_tests()
    
    # 生成汇总报告
    print("\n" + "="*60)
    print("生成汇总报告")
    print("="*60)
    
    await generate_summary_report()
    
    print("\n" + "="*60)
    print("压测完成")
    print("="*60)
    print(f"结束时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")


async def generate_summary_report():
    """生成汇总报告"""
    import json
    from pathlib import Path
    
    results_dir = Path("benchmarks/results")
    
    # 收集所有结果
    all_results = []
    
    for result_file in results_dir.glob("*_results.json"):
        with open(result_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
            all_results.extend(data.get('results', []))
    
    if not all_results:
        print("未找到测试结果")
        return
    
    # 生成汇总报告
    report = []
    report.append("# Actor System 压测汇总报告\n\n")
    report.append(f"**测试时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    report.append(f"**测试场景数**: {len(all_results)}\n\n")
    
    # 性能摘要表格
    report.append("## 性能摘要\n\n")
    report.append("| 测试场景 | 吞吐量 (msg/s) | 平均延迟 (ms) | P99延迟 (ms) | 内存峰值 (MB) |\n")
    report.append("|----------|----------------|---------------|--------------|---------------|\n")
    
    for r in sorted(all_results, key=lambda x: x['test_name']):
        report.append(
            f"| {r['test_name']} | {r['msg_per_sec']:,.0f} | "
            f"{r.get('avg_latency_ms', 0):.2f} | {r.get('p99_latency_ms', 0):.2f} | "
            f"{r.get('peak_memory_mb', 0):.1f} |\n"
        )
    
    # 性能分类
    report.append("\n## 性能分类\n\n")
    
    # 基础性能
    report.append("### 基础性能\n\n")
    basic_tests = [r for r in all_results if 'tell' in r['test_name'] or 'ask' in r['test_name']]
    for r in basic_tests[:5]:  # 只显示前5个
        report.append(f"- {r['test_name']}: {r['msg_per_sec']:,.0f} msg/s\n")
    
    # 系统模式
    report.append("\n### 系统模式对比\n\n")
    mode_tests = [r for r in all_results if 'loop' in r['test_name'] or 'cross' in r['test_name']]
    for r in mode_tests[:5]:
        report.append(f"- {r['test_name']}: {r['msg_per_sec']:,.0f} msg/s\n")
    
    # Agent Layer
    report.append("\n### Agent Layer\n\n")
    agent_tests = [r for r in all_results if 'agent' in r['test_name'] or 'stream' in r['test_name']]
    for r in agent_tests[:5]:
        report.append(f"- {r['test_name']}: {r['msg_per_sec']:,.0f} msg/s\n")
    
    # 保存报告
    report_file = results_dir / "summary_report.md"
    with open(report_file, 'w', encoding='utf-8') as f:
        f.write(''.join(report))
    
    print(f"汇总报告已保存到: {report_file}")
    
    # 保存合并的 JSON 结果
    combined_data = {
        "timestamp": datetime.now().isoformat(),
        "results": all_results,
    }
    combined_file = results_dir / "all_results.json"
    with open(combined_file, 'w', encoding='utf-8') as f:
        json.dump(combined_data, f, indent=2, ensure_ascii=False)
    
    print(f"合并结果已保存到: {combined_file}")


def main():
    """主函数"""
    import argparse
    
    parser = argparse.ArgumentParser(description="Actor System 压测工具")
    parser.add_argument(
        '--basic',
        action='store_true',
        help='只运行基础性能测试'
    )
    parser.add_argument(
        '--modes',
        action='store_true',
        help='只运行系统模式对比测试'
    )
    parser.add_argument(
        '--agent',
        action='store_true',
        help='只运行 Agent Layer 测试'
    )
    parser.add_argument(
        '--stress',
        action='store_true',
        help='运行极限压力测试（默认不运行）'
    )
    parser.add_argument(
        '--all',
        action='store_true',
        help='运行所有测试（包括极限压力测试）'
    )
    
    args = parser.parse_args()
    
    # 确定运行哪些测试
    if args.all:
        run_basic = run_modes = run_agent = run_stress = True
    elif args.basic or args.modes or args.agent or args.stress:
        run_basic = args.basic
        run_modes = args.modes
        run_agent = args.agent
        run_stress = args.stress
    else:
        # 默认运行 P0 和 P1 测试
        run_basic = run_modes = run_agent = True
        run_stress = False
    
    # 运行测试
    asyncio.run(run_all_benchmarks(
        run_basic=run_basic,
        run_modes=run_modes,
        run_agent=run_agent,
        run_stress=run_stress,
    ))


if __name__ == "__main__":
    main()

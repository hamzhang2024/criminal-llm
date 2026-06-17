#!/usr/bin/env python3
"""测试证据质证意见生成功能"""
import asyncio
import sys

# 添加后端目录到路径
sys.path.insert(0, '/Users/zhanghan/.openclaw/workspace/criminal-llm/backend')

from pathlib import Path

from analysis_engine import AnalysisEngine


async def test_cross_examination():
    """测试高为峰案件的证据质证意见生成"""

    case_id = "case_e6486dd5"
    case_path = Path("/Users/zhanghan/Documents/.criminal-llm-data/cases/case_e6486dd5/案件_高为峰开设赌场罪_20260429")

    if not case_path.exists():
        print(f"案件目录不存在: {case_path}")
        return

    print(f"案件ID: {case_id}")
    print(f"案件路径: {case_path}")
    print("-" * 60)

    # 创建分析引擎
    engine = AnalysisEngine(case_id, case_path)

    # 运行证据质证意见生成
    print("开始生成证据质证意见...")
    print()

    try:
        result = await engine.generate_cross_examination_opinion()

        print("=" * 60)
        print("质证意见生成完成!")
        print("=" * 60)
        print()

        # 打印统计信息
        print(f"审查证据总数: {result.get('total_evidence', 0)}")
        print()

        # 打印每份证据的审查结果摘要
        reviews = result.get('reviews', [])
        problematic_count = 0

        for i, review in enumerate(reviews, 1):
            ev_name = review.get('evidence_name', '未知证据')
            ev_ref = review.get('evidence_ref', '')
            ev_type = review.get('evidence_type', '其他证据')

            leg_score = review.get('legality', {}).get('score', 0)
            auth_score = review.get('authenticity', {}).get('score', 0)
            rel_score = review.get('relevance', {}).get('score', 0)

            final_conclusion = review.get('final_conclusion', '存疑')

            has_issues = leg_score < 70 or auth_score < 70 or rel_score < 70
            if has_issues:
                problematic_count += 1

            print(f"[{i}] {ev_ref} - {ev_name}")
            print(f"    类型: {ev_type}")
            print(f"    合法性: {leg_score}分 | 真实性: {auth_score}分 | 关联性: {rel_score}分")
            print(f"    综合结论: {final_conclusion}")

            # 打印发现的问题
            if has_issues:
                leg_findings = review.get('legality', {}).get('findings', [])
                auth_findings = review.get('authenticity', {}).get('findings', [])

                if leg_findings:
                    print("    合法性问题:")
                    for f in leg_findings[:2]:
                        print(f"      - {f.get('issue', '')}")
                        if f.get('legal_basis'):
                            print(f"        法条: {f['legal_basis']}")

                if auth_findings:
                    print("    真实性问题:")
                    for f in auth_findings[:2]:
                        print(f"      - {f.get('issue', '')}")

                # 打印质证策略
                strategy = review.get('legality', {}).get('strategy', [])
                if strategy:
                    print(f"    质证策略: {', '.join(strategy[:2])}")

            print()

        print("=" * 60)
        print(f"问题证据数量: {problematic_count} / {len(reviews)}")
        print("=" * 60)

        # 打印质证意见文档路径
        cross_file = case_path / "analysis" / "cross_examination.md"
        print(f"\n质证意见文档已保存到: {cross_file}")

        # 打印前500字符预览
        if cross_file.exists():
            content = cross_file.read_text(encoding='utf-8')
            print("\n--- 质证意见预览 ---")
            print(content[:1500])
            print("...")

    except Exception as e:
        print(f"错误: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_cross_examination())

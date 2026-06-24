#!/usr/bin/env python3
"""
MinerU 真批量 vs 逐文件并发 实测对比

用同一批 PDF 分别跑两种模式，对比：
- 提交 API 调用次数（POST /file-urls/batch）
- 轮询 API 调用次数（GET /extract-results/batch）
- 总耗时

用法：python3 scripts/bench_mineru.py [文件数]
默认用 3 个小文件（省 API 配额）。
"""
import asyncio
import sys
import time
from pathlib import Path

# 加载后端
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from mineru_async import AsyncMinerUConverter
from mineru_async_helpers import MINERU_API
import aiohttp
import config_manager


# 计数器：统计 API 调用次数
class CallCounter:
    def __init__(self):
        self.submit_calls = 0
        self.poll_calls = 0

    def reset(self):
        self.submit_calls = 0
        self.poll_calls = 0


COUNTER = CallCounter()


def patch_counter(conv):
    """patch 转换器的网络方法，统计调用次数"""
    orig_submit_single = conv._submit_task
    orig_submit_batch = conv._submit_batch_task
    orig_poll = conv._poll_result
    orig_poll_batch = conv._poll_batch_results

    async def counted_submit_single(session, pdf_path, data_id):
        COUNTER.submit_calls += 1
        return await orig_submit_single(session, pdf_path, data_id)

    async def counted_submit_batch(session, files):
        COUNTER.submit_calls += 1
        return await orig_submit_batch(session, files)

    async def counted_poll(session, batch_id, data_id, timeout, progress_cb=None):
        COUNTER.poll_calls += 1
        return await orig_poll(session, batch_id, data_id, timeout, progress_cb)

    async def counted_poll_batch(session, batch_id, file_count, timeout, progress_cb=None):
        COUNTER.poll_calls += 1
        return await orig_poll_batch(session, batch_id, file_count, timeout, progress_cb)

    conv._submit_task = counted_submit_single
    conv._submit_batch_task = counted_submit_batch
    conv._poll_result = counted_poll
    conv._poll_batch_results = counted_poll_batch
    return conv


async def run_legacy(conv, pdfs, output_dir):
    """旧逻辑：逐文件并发（模拟 convert_batch 原实现，max_concurrent=3）"""
    COUNTER.reset()
    semaphore = asyncio.Semaphore(3)

    async def convert_one(pdf):
        async with semaphore:
            return await conv.convert_single(pdf, output_dir, progress_cb=lambda s, d: None)

    t0 = time.time()
    results = await asyncio.gather(*[convert_one(p) for p in pdfs], return_exceptions=True)
    elapsed = time.time() - t0
    return elapsed, COUNTER.submit_calls, COUNTER.poll_calls, results


async def run_batch(conv, pdfs, output_dir):
    """新逻辑：真批量"""
    COUNTER.reset()
    t0 = time.time()
    results = await conv.convert_batch(pdfs, output_dir, progress_cb=lambda p: None)
    elapsed = time.time() - t0
    return elapsed, COUNTER.submit_calls, COUNTER.poll_calls, results


async def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 3

    # 选测试文件：按大小升序取前 n 个
    case_dir = Path.home() / "Documents/.criminal-llm-data/cases/case_c24331de/案件_顾君燕涉嫌诈骗罪_20260607/processed"
    pdfs = sorted(case_dir.glob("*.pdf"), key=lambda p: p.stat().st_size)[:n]
    print(f"测试文件（{len(pdfs)} 个）:")
    for p in pdfs:
        print(f"  {p.stat().st_size // 1024}KB  {p.name}")
    print()

    token = config_manager.get_config_value("mineru_token")
    if not token:
        print("❌ 未配置 mineru_token")
        return

    out1 = Path("/tmp/bench_legacy")
    out2 = Path("/tmp/bench_batch")
    out1.mkdir(exist_ok=True)
    out2.mkdir(exist_ok=True)

    # 旧逻辑
    print("=== 旧逻辑：逐文件并发（max_concurrent=3）===")
    conv1 = patch_counter(AsyncMinerUConverter(token=token))
    try:
        e1, s1, p1, r1 = await run_legacy(conv1, pdfs, out1)
        ok1 = sum(1 for r in r1 if not isinstance(r, Exception) and getattr(r, 'success', False))
        print(f"  耗时: {e1:.1f}s  提交: {s1} 次  轮询: {p1} 次  成功: {ok1}/{len(pdfs)}")
    except Exception as ex:
        print(f"  异常: {ex}")
        e1, s1, p1 = 0, 0, 0

    print()

    # 新逻辑
    print("=== 新逻辑：真批量 ===")
    conv2 = patch_counter(AsyncMinerUConverter(token=token))
    try:
        e2, s2, p2, r2 = await run_batch(conv2, pdfs, out2)
        ok2 = sum(1 for r in r2 if getattr(r, 'success', False))
        print(f"  耗时: {e2:.1f}s  提交: {s2} 次  轮询: {p2} 次  成功: {ok2}/{len(pdfs)}")
    except Exception as ex:
        print(f"  异常: {ex}")
        e2, s2, p2 = 0, 0, 0

    print()
    print("=== 对比 ===")
    print(f"  提交次数: {s1} → {s2}  （减少 {max(0,s1-s2)} 次）")
    print(f"  轮询次数: {p1} → {p2}  （减少 {max(0,p1-p2)} 次）")
    if e1 and e2:
        print(f"  耗时: {e1:.1f}s → {e2:.1f}s  （{'提速' if e2 < e1 else '变慢'} {abs(e1-e2):.1f}s）")


if __name__ == "__main__":
    asyncio.run(main())

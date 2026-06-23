"""
证据提取质量门禁模块

提取完成后对 evidence/index.json 做规则检查，发现异常写 quality_report.json。
前端可读取报告展示告警条。
"""
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def run_quality_gate(evidence_dir: Path, case_id: str = "") -> dict[str, Any]:
    """对证据提取结果运行质量门禁检查

    Args:
        evidence_dir: evidence/ 目录路径
        case_id: 案件 ID（用于报告）

    Returns:
        质量报告字典，同时写入 evidence/quality_report.json
    """
    index_file = evidence_dir / "index.json"
    report: dict[str, Any] = {
        "case_id": case_id,
        "generated_at": datetime.now().isoformat(),
        "alerts": [],
        "stats": {},
        "overall_status": "ok",  # ok / warning / critical
    }

    if not index_file.exists():
        report["overall_status"] = "critical"
        report["alerts"].append({
            "level": "critical",
            "rule": "index_missing",
            "message": "证据清单 index.json 不存在",
        })
        _save_report(evidence_dir, report)
        return report

    try:
        index_data = json.loads(index_file.read_text(encoding="utf-8"))
    except Exception as e:
        report["overall_status"] = "critical"
        report["alerts"].append({
            "level": "critical",
            "rule": "index_parse_error",
            "message": f"index.json 解析失败: {e}",
        })
        _save_report(evidence_dir, report)
        return report

    evidence_list = index_data.get("evidence", [])
    total = len(evidence_list)

    # 统计
    needs_review_count = sum(1 for e in evidence_list if e.get("needs_review"))
    reviewed_count = sum(1 for e in evidence_list if e.get("reviewed"))
    empty_summary_count = sum(1 for e in evidence_list if not (e.get("summary_preview") or "").strip())
    empty_key_facts_count = sum(1 for e in evidence_list if not (e.get("key_facts") or "").strip())
    # contradiction_hints 为空或填"无"都视为未提取到矛盾提示
    empty_hints_count = sum(
        1 for e in evidence_list
        if not (hint := (e.get("contradiction_hints") or "").strip()) or hint == "无"
    )
    type_distribution: dict[str, int] = {}
    for e in evidence_list:
        t = e.get("type", "其他证据")
        type_distribution[t] = type_distribution.get(t, 0) + 1

    report["stats"] = {
        "total_evidence": total,
        "needs_review": needs_review_count,
        "reviewed": reviewed_count,
        "empty_summary": empty_summary_count,
        "empty_key_facts": empty_key_facts_count,
        "empty_contradiction_hints": empty_hints_count,
        "type_distribution": type_distribution,
    }

    # 规则检查
    alerts: list[dict[str, Any]] = []

    # 规则1：全案证据数量异常
    if total == 0:
        alerts.append({"level": "critical", "rule": "zero_evidence", "message": "全案提取到 0 份证据，LLM 可能全部失败"})
    elif total < 5:
        alerts.append({"level": "warning", "rule": "too_few_evidence", "message": f"全案仅 {total} 份证据，可能遗漏（典型案件应有 10+ 份）"})
    elif total > 500:
        alerts.append({"level": "warning", "rule": "too_many_evidence", "message": f"全案 {total} 份证据，可能过度拆分（检查是否有文书被误拆）"})

    # 规则2：降级证据（原始文件）占比过高
    if total > 0:
        needs_review_ratio = needs_review_count / total
        if needs_review_ratio > 0.3:
            alerts.append({
                "level": "critical",
                "rule": "high_needs_review_ratio",
                "message": f"需人工复核的证据占 {needs_review_ratio:.0%}（{needs_review_count}/{total}），LLM 解析失败率过高",
            })
        elif needs_review_ratio > 0.1:
            alerts.append({
                "level": "warning",
                "rule": "medium_needs_review_ratio",
                "message": f"需人工复核的证据占 {needs_review_ratio:.0%}（{needs_review_count}/{total}）",
            })

    # 规则3：空 summary 占比
    if total > 0 and empty_summary_count / total > 0.2:
        alerts.append({
            "level": "warning",
            "rule": "high_empty_summary",
            "message": f"{empty_summary_count}/{total} 份证据的摘要为空",
        })

    # 规则4：空 key_facts 占比
    if total > 0 and empty_key_facts_count / total > 0.5:
        alerts.append({
            "level": "warning",
            "rule": "high_empty_key_facts",
            "message": f"{empty_key_facts_count}/{total} 份证据的关键事实为空，影响下游分析质量",
        })

    # 规则5：类型分布异常（全案只有一种类型）
    if len(type_distribution) == 1 and total > 10:
        only_type = list(type_distribution.keys())[0]
        alerts.append({
            "level": "warning",
            "rule": "single_type",
            "message": f"全案 {total} 份证据全部为「{only_type}」，可能证据类型识别有误",
        })

    report["alerts"] = alerts
    # 综合状态
    if any(a["level"] == "critical" for a in alerts):
        report["overall_status"] = "critical"
    elif any(a["level"] == "warning" for a in alerts):
        report["overall_status"] = "warning"
    else:
        report["overall_status"] = "ok"

    _save_report(evidence_dir, report)
    logger.info(f"[质量门禁] case={case_id} 状态={report['overall_status']} 告警数={len(alerts)}")
    return report


def _save_report(evidence_dir: Path, report: dict[str, Any]) -> None:
    """保存质量报告到 evidence/quality_report.json"""
    report_file = evidence_dir / "quality_report.json"
    try:
        report_file.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning(f"[质量门禁] 保存报告失败: {e}")

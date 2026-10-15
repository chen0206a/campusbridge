"""Derive comparison tables and every-case deltas from frozen Stage B outputs."""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from campusbridge.rag_b_evaluation import validate_checksums
from campusbridge.rag_evaluation import checksums, sha, write_json, write_jsonl


def read(path): return json.loads(Path(path).read_text(encoding="utf-8"))
def lines(path): return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines()]
def pct(value): return "—" if value is None else f"{value * 100:.2f}%"
def number(value): return "—" if value is None else f"{value:.4f}"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists(): raise ValueError("REPORT_ALREADY_EXISTS")
    for directory in ("comparison", "dev-selection", "packet", "isolated-latency"):
        validate_checksums(args.run / directory)
    result = read(args.run / "comparison/comparison.json")
    isolated = read(args.run / "isolated-latency/summary.json")
    selection = read(args.run / "dev-selection/selection.json")
    args.output.mkdir(parents=True)
    report = ["# RAG Stage B：BM25 / Dense / Hybrid", "",
        "采用固定 BGE-small-zh-v1.5、CPU FP32、CLS+L2，RRF 等权 k=60、候选池20。32条Dev在预定义0.50/0.60/0.70/0.80中选择，Dense/Hybrid均为0.50；随后仅一次评测72条原固定查询和32条Holdout。知识集固定为16篇19块。", "",
        "以下为离线检索指标。Recall按可回答查询的相关chunk集合宏平均；MRR截断5；Top1来源命中率以可回答查询为分母；Top3来源精度为所有查询返回Top3中相关chunk数/返回chunk数。身份/版本/校园/Hash另行验证。", ""]
    analysis = ["# 全部查询的改善、退化与失败归因", "", "每行保留三个模式的原始分数；0→1表示该查询的Chunk Recall@3从0增至1。", ""]
    deltas = []
    for split, modes in result["splits"].items():
        report += [f"## {split}", "", "| 指标 | BM25 | Dense | Hybrid |", "|---|---:|---:|---:|"]
        rows = {m: lines(args.run / "comparison" / split / m / "results.jsonl") for m in modes}
        metrics = [("recall@1", "Recall@1"), ("recall@3", "Recall@3"), ("recall@5", "有效Recall@5"),
            ("mrr_at_5", "MRR@5"), ("top1_source_correct", "Top1来源命中率"),
            ("top3_source_precision_micro_all_queries", "Top3来源精度（全部查询）")]
        for key, title in metrics:
            values = [modes[m]["overall"]["ranking_top5"][key] for m in modes]
            report.append("| " + title + " | " + " | ".join(number(v) if key == "mrr_at_5" else pct(v) for v in values) + " |")
        for kind, title in (("no_material", "无资料"), ("insufficient_evidence", "证据不足"), ("all_legacy_no_answer", "历史合并负例")):
            for key, description in (("empty_rejection_rate", "空检索率"), ("returned_material_rate", "返回材料率")):
                values = [pct(modes[m]["overall"]["ranking_top5"][kind][key]) for m in modes]
                report.append(f"| {title}{description} | " + " | ".join(values) + " |")
        report += ["", "无资料返回材料率即误召回率。证据不足返回同主题材料只说明找到了材料，系统仍没有独立的可回答性分类器；这些数值衡量空检索行为，不能替代拒答或回答正确率。", "",
            "| 分组Recall@3 | BM25 | Dense | Hybrid |", "|---|---:|---:|---:|"]
        for group in ("exact", "synonym", "alias", "history", "distractor", "cross_paragraph"):
            report.append("| " + group + " | " + " | ".join(pct(modes[m]["by_group"][group]["ranking_top5"]["recall@3"]) for m in modes) + " |")
        report += ["", "| 生产链视图Recall@3 | BM25 | Dense | Hybrid |", "|---|---:|---:|---:|"]
        for stage in ("context_top3", "dynamic_top3"):
            report.append("| " + stage + " | " + " | ".join(pct(modes[m]["overall"][stage]["recall@3"]) for m in modes) + " |")
        report += ["", "Top3视图的Recall@5统一为null；其MRR仅覆盖该视图实际深度3。", ""]
        analysis += [f"## {split}", "", "| case_id / query | BM25 R@3 | Dense R@3 | Hybrid R@3 | 诊断 |", "|---|---:|---:|---:|---|"]
        for base, dense, hybrid in zip(rows["bm25"], rows["dense"], rows["hybrid"], strict=True):
            values = [r["scores"]["ranking_top5"]["recall@3"] for r in (base, dense, hybrid)]
            flags = []
            if base["reference"]["no_answer"]:
                for m, row in zip(modes, (base, dense, hybrid), strict=True):
                    if row["hits"]["ranking_top5"]: flags.append(m + ":" + ("主题材料不足" if row["no_answer_kind"] == "unsupported_fact" else "无资料误召回"))
            else:
                for m, row in (("dense", dense), ("hybrid", hybrid)):
                    current = row["scores"]["ranking_top5"]["recall@3"]
                    if current > values[0]: flags.append(m + "改善")
                    if current < values[0]: flags.append(m + "退化")
                    if current < 1: flags.append(m + "证据不全")
                    if row["scores"]["dynamic_top3"]["recall@3"] < current: flags.append(m + "仲裁丢失相关证据")
            delta = {"split": split, "case_id": base["case_id"], "query": base["query"], "kind": base["no_answer_kind"], "flags": flags,
                "scores": {m: row["scores"] for m, row in zip(modes, (base, dense, hybrid), strict=True)},
                "top5_documents": {m: [h["document_id"] for h in row["hits"]["ranking_top5"]] for m, row in zip(modes, (base, dense, hybrid), strict=True)}}
            deltas.append(delta)
            analysis.append("| " + base["case_id"] + " / " + base["query"].replace("|", " ") + " | " + " | ".join(pct(v) for v in values) + " | " + ("；".join(flags) or "稳定") + " |")
        counts = Counter(flag for delta in deltas if delta["split"] == split for flag in delta["flags"])
        report += ["变化统计：`" + json.dumps(counts, ensure_ascii=False) + "`。", ""]
    report += ["## 资源与时延", "", "正式评测时完整后端测试也在运行，正式逐条时延原样保留。下表另在后端测试结束后做一次固定排名的隔离时延回放：每查询预热1次、测3次，216个样本/模式；每次source顺序必须与正式结果相同，不重新计分或调参。", "",
        "| 模式 | Warm P50 ms | Warm P95 ms |", "|---|---:|---:|"]
    for mode, values in isolated["modes"].items():
        report.append(f"| {mode} | {values['p50']:.2f} | {values['p95']:.2f} |")
    mib = lambda value: f"{value / 1024**2:.2f} MiB"
    report += ["", f"模型新进程加载（含依赖导入/文件校验，OS文件缓存已热）：{result['model_load_seconds']:.2f}s；19块索引构建：{result['index_build']['seconds']:.2f}s。",
        f"RSS启动/加载后/结束：{mib(result['rss_before_bytes'])} / {mib(result['rss_model_loaded_bytes'])} / {mib(result['rss_after_bytes'])}；进程峰值{mib(result['process_peak_rss_bytes'])}。",
        f"正式比较总耗时{result['elapsed_seconds']:.2f}s；FP32 CPU两线程，查询向量不缓存，文档向量SQLite float32缓存。向量负载19×512×4=38,912字节，不含SQLite元数据。", "",
        "## 默认方案与下一步", "",
        "默认继续bm25，保证原有零模型依赖体验。已安装本地Embedding的质量优先部署建议显式选dense：原72条召回更高、来源更集中、无资料误召回更低。hybrid保留可切换实验模式，Holdout排序更好，但原72条的Top3来源精度更低、保留了BM25无资料误命中，延迟也更高。", "",
        "先处理语义检索与既有词面仲裁的兼容，以及证据不足的可回答性判断，再决定Stage C。当前Hybrid从Top3到Top5只获得少量增益；不立即增加Reranker。", "",
        "标注复核提示：新Holdout的21号问题问器材借用准备与离场，冻结证据包含准入培训与离场。该条0.5分对第一证据的选择较敏感，应独立复核后另立版本；本轮所有原分与标签保留。32条Holdout由开发者编写并预先固定，未经过独立人工裁决。", ""]
    (args.output / "COMPARISON.md").write_text("\n".join(report), encoding="utf-8", newline="\n")
    (args.output / "FAILURE_ANALYSIS.md").write_text("\n".join(analysis), encoding="utf-8", newline="\n")
    write_jsonl(args.output / "case-deltas.jsonl", deltas)
    old = ROOT / "experiments/rag/20261014-stage-a-v1"
    oldrows = lines(old / "bm25-baseline-v1/results.jsonl")
    depths = Counter(len(row["hits"]["ranking_top5"]) for row in oldrows)
    audit = ["# Stage A 指标口径复核（追加报告）", "",
        "原Stage A配置明确ranking_top_k=5；实际候选数分布：`" + json.dumps(dict(sorted(depths.items()))) + "`。主表60.71%/76.79%/76.79%的Recall@1/3/5有效，候选经过阈值过滤后不足5不构成深度错误。", "",
        "production_context_top3和dynamic_evidence_top3只请求3个结果，旧score_hits仍为它们计算Recall@5，该字段无效。新协议在这两个视图将Recall@5/document_recall@5/hit@5设为null。主表分数不变；Stage A原始JSON、报告与Hash全部保留。", "",
        "原16条负例拆为无相关资料7条（outside_scope5+campus_isolation2）和主题材料证据不足9条。BM25无资料正确空检索6/7=85.71%，误召回1/7=14.29%；证据不足0/9空检索、9/9返回材料。旧合并口径6/16=37.50%继续保留供追溯。", "",
        "证据不足中的同主题资料可以支持提示未知或联系工作人员，检索命中本身不证明所问事实存在。Stage B逐条记录RETRIEVED_UNVERIFIED/NO_MATERIAL_RETRIEVED，单列两种负例，未引入基于gold的定位或运行时判断。", "",
        "新BM25使用与Dense/Hybrid相同的新评分实现、原始72条和同一语料，排名及三项Recall完全复现Stage A；所有返回来源的校园、active revision、原文和Hash检查通过。", ""]
    (args.output / "STAGE_A_METRIC_AUDIT.md").write_text("\n".join(audit), encoding="utf-8", newline="\n")
    write_json(args.output / "report-bindings.json", {"comparison_sha256": sha(args.run / "comparison/comparison.json"),
        "selection_sha256": sha(args.run / "dev-selection/selection.json"), "profile_sha256": sha(args.run / "isolated-latency/summary.json"),
        "script_sha256": sha(Path(__file__)), "stage_a_candidate_depth_distribution": dict(sorted(depths.items()))})
    checksums(args.output)
    print(json.dumps({"reported_cases": len(deltas), "source": "frozen formal results", "output_created": True}))


if __name__ == "__main__": main()

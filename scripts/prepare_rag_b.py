"""Freeze independently authored Dev/Holdout and a verbatim Stage A comparison set."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from campusbridge.db import Database
from campusbridge.rag_evaluation import checksums, load_cases, resolve_labels, sha, write_json, write_jsonl
from campusbridge.retrieval import SQLiteKnowledge
import tempfile

# Authored before any embedding retrieval or parameter selection. No model outputs used.
DEV = [
 ("exact", "图书馆借书先准备哪些资料？", "campus-library"),
 ("exact", "打印复印提交文件前如何检查？", "rag-print"),
 ("exact", "无障碍学习环境调整从哪里开始？", "campus-access"),
 ("exact", "课程冲突想调整要提供什么？", "campus-course"),
 ("synonym", "饭钱不够了，有没有临时帮一把的办法？", "rag-bursary"),
 ("synonym", "想找个人私下聊聊烦心事，怎样约？", "campus-support"),
 ("synonym", "屋里的水龙头一直滴答，该找谁？", "campus-housing"),
 ("synonym", "借的读物不舍得还，能延期吗？", "campus-library"),
 ("alias", "一卡通不见了要先做什么？", "rag-idcard"),
 ("alias", "WLAN 登录不了该留下什么信息？", "rag-wifi"),
 ("alias", "实验室入场安全学习如何核验？", "rag-lab"),
 ("alias", "成绩奖励的申报资料怎么整理？", "rag-merit"),
 ("history", "那要交代哪些情况？", "campus-housing"),
 ("history", "这个需要公开银行记录吗？", "rag-bursary"),
 ("history", "刚才说的卡应该先停用吗？", "rag-idcard"),
 ("history", "它需要写几个人参加吗？", "rag-quiet"),
 ("distractor", "要了解打印的隐私注意事项，和网络认证无关。", "rag-print"),
 ("distractor", "学习环境的通行调整怎样申请，不是借用房间。", "campus-access"),
 ("distractor", "社团活动结束还设备需要哪些检查，不问实验准入。", "rag-room"),
 ("distractor", "只想查借阅资格，不准备约讨论室。", "campus-library"),
 ("cross_paragraph", "教学实验空间进去前与离开前分别怎么确认？", "rag-lab"),
 ("cross_paragraph", "社团申请场地到活动后归还分别要说明什么？", "rag-room"),
 ("cross_paragraph", "图书馆约座位与续借需要分别准备什么？", "campus-library"),
 ("cross_paragraph", "无线网络故障要记录什么，密码忘了又怎么处理？", "rag-wifi"),
 ("outside_scope", "护照换发在国外该交哪些材料？", None),
 ("outside_scope", "宠物疫苗一年打几次？", None),
 ("outside_scope", "帮我比较两款汽车的保养成本。", None),
 ("outside_scope", "请给我海鲜意面的食谱。", None),
 ("unsupported_fact", "活动空间最多容纳多少人？", None),
 ("unsupported_fact", "宿舍无线网每人带宽具体是多少？", None),
 ("campus_isolation", "图书馆的座位要怎么预约？", None),
 ("campus_isolation", "校园卡丢失应当怎么办？", None),
]
HOLDOUT = [
 ("exact", "失物招领登记需要物品什么特征？", "campus-lost"),
 ("exact", "核对考试安排之前整理哪些信息？", "campus-exam"),
 ("exact", "学业奖学金需要成绩还是困难说明？", "rag-merit"),
 ("exact", "讨论室预约由谁提交申请？", "rag-quiet"),
 ("synonym", "钥匙落哪儿记不清了，去哪里报个丢失？", "campus-lost"),
 ("synonym", "课业堆成山了，怎样迈出第一小步？", "campus-exam"),
 ("synonym", "房间灯一闪一闪的，找人来看看前怎么说？", "campus-housing"),
 ("synonym", "想把电子稿弄成纸质的，要先检查啥？", "rag-print"),
 ("alias", "退选某门课要找谁核对手续？", "campus-course"),
 ("alias", "校园现金救急支持先讲什么困难？", "rag-bursary"),
 ("alias", "行动不便的同学想调整上课环境怎么沟通？", "campus-access"),
 ("alias", "学生谈心预约要说明方便通话的时段吗？", "campus-support"),
 ("history", "那最后领取还要核验吗？", "campus-lost"),
 ("history", "上述流程有变化时要怎么说？", "rag-room"),
 ("history", "这个该先列什么再休息？", "campus-exam"),
 ("history", "它丢了之后还要报告什么时候用过吗？", "rag-idcard"),
 ("distractor", "关注生活困难不是学业奖励：临时补助要怎么申请？", "rag-bursary"),
 ("distractor", "请说宿舍窗户损坏登记材料，不要无线网络排障。", "campus-housing"),
 ("distractor", "卡片停用和失物招领不同，遗失校园卡先联系谁？", "rag-idcard"),
 ("distractor", "个人图书馆安静区域座位如何确认，不是小组空间。", "campus-library"),
 ("cross_paragraph", "实验空间借器材前与归还离场各要核对什么？", "rag-lab"),
 ("cross_paragraph", "社团活动前预约和结束后遗留物品怎样登记？", "rag-room"),
 ("cross_paragraph", "想约支持咨询又不愿详细讲，能怎样开始？", "campus-support"),
 ("cross_paragraph", "借书要确认归还时间，约座位要确认哪些安排？", "campus-library"),
 ("outside_scope", "民事诉讼上诉期有多长？", None),
 ("outside_scope", "天文望远镜怎么校准赤道仪？", None),
 ("outside_scope", "请写一个蒸汽机发展史提纲。", None),
 ("outside_scope", "怎么计算烘焙面团的含水率？", None),
 ("unsupported_fact", "打印服务黑白一页收费几分钱？", None),
 ("unsupported_fact", "图书逾期的罚款每天是多少？", None),
 ("campus_isolation", "能介绍一下困难资助的核对流程吗？", None),
 ("campus_isolation", "在哪里登记宿舍设施故障呢？", None),
]

ANCHORS = {
 "campus-library": ["图书馆座位预约：先确定日期、时间段和需要的安静区域", "图书借阅与续借：准备需要借阅的书名或馆藏编号"],
 "campus-housing": ["普通设施报修先说明设施类型、所在位置、故障现象和可进入维修的时间段"],
 "campus-support": ["说明希望讨论的事情、可联系的时间段和偏好的沟通方式", "暂时不愿详细讲述时，可以先说明希望获得哪类支持"],
 "campus-access": ["说明具体困难、希望获得的调整和适合联系的方式"],
 "campus-course": ["整理课程名称、发生冲突的时间段、希望调整的课程"],
 "campus-lost": ["记录最后看见物品的位置、时间段和可辨识特征"],
 "campus-exam": ["先整理课程名称、目前看到的考试时间和冲突之处", "学习规划可以先列出近期任务，选择一个最重要且能完成的小步骤"],
 "rag-print": ["打印前确认页数、单面或双面、彩色或黑白以及纸张尺寸"],
 "rag-bursary": ["说明最近生活支出困难、急需解决的事项和适合联系的方式"],
 "rag-idcard": ["先联系卡务窗口核实挂失流程，再说明最后使用的时间段与是否需要补卡"],
 "rag-wifi": ["先记录设备类型、所在楼栋、认证提示和故障出现时间", "忘记无线认证口令时通过网络工作人员核实重置流程"],
 "rag-quiet": ["记录团队人数、讨论主题和希望使用的时间段"],
 "rag-merit": ["整理课程成绩记录与活动证明交给学生事务人员核对"],
 "rag-lab": ["先完成安全培训，再由实验值班人员核对培训完成记录", "离开前向实验值班人员交回器材并确认现场检查事项"],
 "rag-room": ["先提供活动主题、参与人数和时间段", "归还借用设备并确认桌椅复位和物品清点"],
}
HISTORIES = {
 "campus-housing": "我需要了解宿舍设施报修。", "rag-bursary": "刚才在讨论临时生活补助。",
 "rag-idcard": "我想了解校园卡遗失挂失。", "rag-quiet": "我在问小组讨论室预约。",
 "campus-lost": "我需要失物招领的流程。", "rag-room": "刚才在问社团活动空间申请。",
 "campus-exam": "我想做学习规划。",
}


def main():
    output = ROOT / "eval/rag/20261015-stage-b-v1"
    if output.exists():
        raise ValueError("DATASET_ALREADY_FROZEN")
    source = ROOT / "experiments/rag/20261014-stage-a-v1/packet"
    corpus = json.loads((source / "corpus.json").read_text(encoding="utf-8"))
    output.mkdir(parents=True)
    (output / "stage-a.jsonl").write_bytes((source / "queries.jsonl").read_bytes())
    write_json(output / "corpus.json", corpus)
    for split, values in (("dev", DEV), ("holdout", HOLDOUT)):
        rows = []
        for n, (group, query, doc) in enumerate(values, 1):
            negative = doc is None
            quotes = ANCHORS.get(doc, [])
            if group != "cross_paragraph":
                quotes = quotes[:1]
            rows.append({"case_id": f"rag-a-b-{split}-{n:02d}", "group": "no_answer" if negative else group,
                "campus_id": "fictional-campus-b" if group == "campus_isolation" else "fictional-campus-a",
                "query": query, "history": [{"role": "user", "text": HISTORIES[doc]}] if group == "history" else [],
                "no_answer": negative, "no_answer_kind": group if negative else "none",
                "evidence": [{"document_id": doc, "quote": quote} for quote in quotes],
                "rationale": "原创独立查询；证据按文档原文绑定。" if doc else "资料缺失" if group != "unsupported_fact" else "主题相关但文档没有所问数值。",
                "source_generation": "original_synthetic"})
        write_jsonl(output / f"{split}.jsonl", rows)
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "index.sqlite3")
        try:
            SQLiteKnowledge(db).import_documents(corpus)
            seen = set()
            for split in ("stage-a", "dev", "holdout"):
                cases = load_cases(output / f"{split}.jsonl")
                for case in cases:
                    key = (case.campus_id, case.query, tuple(h.text for h in case.history))
                    if key in seen:
                        raise ValueError("CROSS_SPLIT_DUPLICATE")
                    seen.add(key)
                write_jsonl(output / f"{split}-references.jsonl", resolve_labels(db, cases))
        finally:
            db.close()
    write_json(output / "protocol.json", {"version": "rag-stage-b-v1", "model_id": "BAAI/bge-small-zh-v1.5",
        "model_revision": "7999e1d3359715c523056ef9478215996d62a620", "ranking_depth": 5, "context_depth": 3,
        "candidate_k": 20, "rrf_k": 60, "equal_rrf_weights": True, "threshold_grid": [0.5, 0.6, 0.7, 0.8],
        "selection": "per mode maximize 0.5*dev chunk Recall@3 + 0.5*dev no-answer empty rejection; ties prefer larger threshold",
        "fixed_before_embedding_calls": True, "query_cache": False, "timed_repeats": 3,
        "insufficient_evidence": "unsupported_fact separated; retrieval does not certify answerability",
        "labels": "developer-authored synthetic, not independent human adjudication",
        "stage_a_original_queries_sha256": sha(source / "queries.jsonl")})
    write_json(output / "provenance.json", {"corpus": "verbatim Stage A 16 original synthetic MIT documents",
        "dev": "32 new queries for threshold selection only", "holdout": "32 new queries, frozen before selection, evaluated once",
        "stage_a": "72 original queries and labels unchanged", "no_llm_used": True})
    checksums(output)
    print(json.dumps({"frozen": True, "stage_a": 72, "dev": len(DEV), "holdout": len(HOLDOUT)}, ensure_ascii=False))


if __name__ == "__main__":
    main()

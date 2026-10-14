import json

from campusbridge.contracts import AgentName, ModelReply
from campusbridge.policy import explicit_danger, explicit_human_request


class MockModel:
    """Deterministic local demonstration model; no network client or model weights."""
    provider_name = "mock"
    model_name = "campusbridge-mock-v3"
    async def complete(self, request):
        payload = json.loads(request.messages[-1].content)
        if request.task == AgentName.INTENT:
            message = payload["message"]
            intent_text = message
            if any(word in message for word in ("那", "刚才", "上述", "这个", "它")):
                previous = [item["text"] for item in payload["history"] if item["role"] == "user"]
                if previous:
                    intent_text += " " + previous[-1]
            knowledge = any(word in intent_text for word in ("预约", "图书馆", "校园服务", "奖助", "助学金", "资助", "教务",
                "选课", "宿舍", "报修", "失物", "借阅", "无障碍", "学生支持", "考试安排"))
            intent = "URGENT" if explicit_danger(message) else "CAMPUS_SERVICE" if knowledge else (
                "SUPPORT" if any(word in message for word in ("焦虑", "压力", "担心", "睡不着")) else "GENERAL")
            content = {"intent": intent, "needs_knowledge": knowledge, "needs_human_support": explicit_human_request(message)}
        elif request.task == AgentName.RISK:
            high = explicit_danger(payload["message"])
            human = explicit_human_request(payload["message"])
            content = {"level": "HIGH" if high else "LOW", "needs_human_support": high or human,
                       "reason_codes": ["EXPLICIT_DANGER" if high else "HUMAN_REQUEST" if human else "NO_SIGNAL"]}
        elif request.task == AgentName.RESPONSE:
            context = payload["context"]
            if context["route"] == "HUMAN_SUPPORT":
                if payload["risk"]["level"] == "HIGH":
                    content = "谢谢你告诉我。先确认你现在是否安全，请联系身边可信任的人或校内值班人员一起获得支持。若存在即时危险，请联系当地紧急服务。你现在身边有可以联系的人吗？"
                else:
                    content = "你的人工支持事项已提交，等待校内工作人员跟进。你希望工作人员先协助哪件事？"
            elif context["knowledge"]:
                item = context["knowledge"][0]
                content = "根据演示校园资料，" + item["text"] + "[source:" + item["source_id"] + "]你希望先了解哪个步骤？"
            elif context.get("retrieval_status") in {"NO_HIT", "ERROR"}:
                content = "这次没有检索到与你的问题相关的校园资料。我不会猜测具体规则，你可以补充事项名称，或向校内工作人员确认。"
            else:
                previous = [item["text"] for item in context["history"] if item["role"] == "user"]
                lead = "你上一轮提到的事情我们可以接着梳理。" if previous else "谢谢你来聊聊。"
                content = lead + "我们先选一个当前最在意的问题，再一起找一个小步骤。你最希望先处理哪件事？"
        elif request.task == AgentName.REVIEW:
            candidate = payload["candidate"]
            content = {"candidate_id": candidate["candidate_id"], "content_hash": candidate["content_hash"],
                       "citations_hash": candidate.get("citations_hash"), "verdict": "PASS", "reason_codes": []}
        else:
            raise ValueError("Unsupported mock task")
        return ModelReply(content=content, input_tokens=48, output_tokens=40)

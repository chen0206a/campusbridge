from campusbridge.contracts import KnowledgeItem


class DemoKnowledge:
    """Own fictional reference cards; replace this port with retrieval later."""
    async def search(self, query, campus_id):
        if campus_id != "fictional-campus-a":
            return ()
        if "图书馆" in query:
            return (KnowledgeItem(source_id="campus-demo-library", title="演示图书馆服务",
                text="演示图书馆的预约方式由校内服务台确认；先准备学号和希望使用的时间段。", revision="original-v1"),)
        return (KnowledgeItem(source_id="campus-demo-support", title="演示学生支持预约",
            text="在这个虚构校园中，学生可以向学生支持值班人员提出预约意愿，再由工作人员确认合适的时间。", revision="original-v1"),)

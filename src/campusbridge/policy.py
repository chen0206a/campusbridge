import re

from campusbridge.contracts import RiskLevel, RiskOutput, ReviewOutput, Verdict, digest

SAFE_REPLY = "我在这里。我们可以先暂停一下，把你目前最需要的支持说清楚。若你此刻担心自己或他人的安全，请马上联系身边可信任的人、校内值班人员或当地紧急服务。"


def sanitized_input(text):
    text = re.sub(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", "[联系方式]", text)
    text = re.sub(r"(?<![0-9])1[0-9]{10}(?![0-9])", "[联系方式]", text)
    return re.sub(r"(?:sk-[A-Za-z0-9_-]{16,}|Bearer\s+[A-Za-z0-9_-]{20,})", "[访问凭据]", text)


def explicit_danger(message):
    phrases = ("想自杀", "想轻生", "我要伤害自己", "想伤害别人", "无法保证自己的安全", "准备伤害自己")
    return any(phrase in message for phrase in phrases)


def explicit_human_request(message):
    """Current first-person request, excluding quoted, negated and information-only clauses."""
    unquoted = re.sub(r'“[^”]*”|「[^」]*」|"[^"\n]*"|\u2018[^\u2019]*\u2019', '', message)
    for clause in re.split(r"[。！？!?；;\n]", unquoted):
        if re.search(r"不(?:想|需要|希望|用|要)|暂时不用|别帮我|无需|只是.*(?:例句|引用)|(?:他说|她说|同学说)", clause):
            continue
        if re.search(r"(?:想|希望|需要)(?:知道|了解|查询)|(?:如何|怎么|怎样|哪里).*(?:联系|预约)|(?:联系|预约).*(?:方式|流程|电话|条件)", clause):
            continue
        if re.search(r"(?:我(?:现在)?(?:想|希望|需要|打算)|请).{0,18}(?:找|联系|约|请|安排|转接).{0,8}(?:辅导员|老师|咨询师|工作人员|真人|人工|学生支持人员)", clause):
            return True
        if re.search(r"(?:我(?:现在)?(?:需要|希望)|请).{0,8}(?:有人|一个人|工作人员|真人).{0,8}(?:帮我|陪我|协助我|和我谈|跟我聊)", clause):
            return True
        if re.search(r"(?:帮我|替我)(?:联系|预约|转接).{0,8}(?:辅导员|老师|咨询师|工作人员|人工)", clause):
            return True
    return False


def combine_risk(message, assessment, intent=None):
    if explicit_danger(message):
        return RiskOutput(level=RiskLevel.HIGH, needs_human=True, reason_codes=("EXPLICIT_DANGER",))
    requested = explicit_human_request(message) or bool(intent and intent.needs_human_support)
    needs_human = requested or assessment.needs_human_support or assessment.level in {RiskLevel.HIGH, RiskLevel.UNKNOWN}
    reasons = tuple(code for code in assessment.reason_codes if code != "NO_SIGNAL") if requested else assessment.reason_codes
    if requested and "HUMAN_REQUEST" not in reasons:
        reasons += ("HUMAN_REQUEST",)
    return assessment.model_copy(update={"needs_human_support": needs_human, "reason_codes": reasons})


def output_violation(candidate, risk):
    content = candidate.text
    code = None
    if any(term in content for term in ("不要联系任何人", "隐瞒所有人", "按我说的伤害")):
        code = "UNSAFE_INSTRUCTION"
    elif re.search(r"(?:你被确诊|你患有|诊断为|你得了抑郁症)[^。\n]*", content):
        code = "DIAGNOSIS"
    elif re.search(r"\bsk-[A-Za-z0-9_-]{16,}|Bearer\s+[A-Za-z0-9_-]{20,}|(?<!\d)1\d{10}(?!\d)|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", content):
        code = "PRIVACY_DISCLOSURE"
    elif re.search(r"risk_level\s*[:=]|风险等级\s*[:：]", content, re.I):
        code = "INTERNAL_LABEL"
    elif risk.level == RiskLevel.HIGH and not all(term in content for term in ("安全", "联系")):
        code = "SAFETY_GUIDANCE_MISSING"
    if code:
        return ReviewOutput(candidate_id=candidate.candidate_id, content_hash=candidate.content_hash,
                            citations_hash=candidate.citations_hash, verdict=Verdict.RETRY, reason_codes=(code,), authority="RULE")
    return None


def valid_binding(candidate, review):
    return (candidate.candidate_id == review.candidate_id and candidate.content_hash == review.content_hash
            and candidate.citations_hash == review.citations_hash and digest(candidate.text) == candidate.content_hash)


def template_approval(candidate):
    if candidate.origin != "SAFE_TEMPLATE" or candidate.text != SAFE_REPLY or candidate.content_hash != digest(SAFE_REPLY):
        raise ValueError("SAFE_TEMPLATE_IDENTITY_FAILED")
    return ReviewOutput(candidate_id=candidate.candidate_id, content_hash=candidate.content_hash,
                        verdict=Verdict.PASS, reason_codes=("APPROVED_TEMPLATE",), authority="TEMPLATE")


def permit_delivery(candidate, review):
    if not valid_binding(candidate, review) or review.verdict != Verdict.PASS:
        raise ValueError("UNREVIEWED_CANDIDATE")
    if candidate.origin == "SAFE_TEMPLATE":
        if review != template_approval(candidate):
            raise ValueError("TEMPLATE_APPROVAL_FAILED")
    elif review.authority != "MODEL":
        raise ValueError("MODEL_APPROVAL_REQUIRED")

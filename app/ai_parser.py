import json
import re
from datetime import datetime, timedelta
from urllib import error, request

from flask import current_app

from .tasks import DATE_FORMAT


AI_FIELDS = (
    "course_name",
    "title",
    "task_type",
    "deadline",
    "description",
    "submission_requirements",
    "required_materials",
    "suggested_materials",
    "estimated_minutes",
    "priority",
    "source_quote",
    "confidence",
    "uncertain_fields",
)
ALLOWED_TASK_TYPES = ("作业", "实验", "阅读", "小论文", "考试", "项目", "其他", "")
ALLOWED_CONFIDENCE = ("原文明确", "AI推断", "信息缺失", "本地演示")
CHAT_SYSTEM_PROMPT = """
你是“课迹”学习任务助手，面向大学生使用。
你的职责分为两类：
一、普通对话：当用户进行问候、感谢、询问功能、询问使用方式、表达学习压力，或进行与学习任务相关的普通交流时，自然、简短地回答。普通对话不能生成任务，不能保存任务，不能虚构任务字段。
二、任务识别：当用户提供作业、考试、实验、论文、报告、项目、阅读、复习、课程通知、截止时间、提交要求、材料要求等信息时，判断为任务，并提取结构化字段。
你必须只返回合法 JSON，不要返回 Markdown，不要返回 JSON 外的解释。
普通对话格式：
{"type":"chat","reply":"给用户的自然回复"}
任务信息格式：
{"type":"task","reply":"给用户的简短说明","task":{"course_name":"","title":"","task_type":"","deadline":"","description":"","submission_requirements":"","required_materials":[],"suggested_materials":[],"estimated_minutes":0,"priority":"中","source_quote":"","confidence":"原文明确","uncertain_fields":[]}}
规则：
- “你好”“谢谢”“你是谁”“你能做什么”“怎么使用”属于普通对话。
- 不要把普通聊天误判为任务。
- 任务信息不完整时，字段可以为空，并把字段名写入 uncertain_fields。
- 截止日期不明确时不要猜测。
- 只有用户提供通知日期时，才可以推算“下周三”等相对日期。
- task_type 只能是：作业、实验、阅读、小论文、考试、项目、其他、空字符串。
- priority 只能是：低、中、高。
- confidence 只能是：原文明确、AI推断、信息缺失。
- required_materials、suggested_materials、uncertain_fields 必须是字符串数组。
- estimated_minutes 必须是非负整数。
- 不得直接保存任务，不得声称已保存任务。
- 回复要自然、简短，不要使用宣传口吻。
""".strip()


class AIParseError(Exception):
    def __init__(self, message):
        super().__init__(message)
        self.message = message


def parse_text_notice(notice_text, notice_date=""):
    notice_text = (notice_text or "").strip()
    notice_date = (notice_date or "").strip()
    if not notice_text:
        raise AIParseError("请先粘贴文字通知。")

    if current_app.config.get("AI_DEMO_MODE") or not current_app.config.get("AI_API_KEY"):
        data = _demo_parse(notice_text, notice_date)
        return {
            "data": data,
            "mode": "demo",
            "reply": "我先用本地演示模式整理了一版任务草稿，请你重点确认缺失或推断字段。",
        }

    content = _call_model(notice_text, notice_date)
    data = parse_model_json(content)
    return {
        "data": data,
        "mode": "ai",
        "reply": "我已根据通知整理出任务草稿，请确认后再保存。",
    }


def parse_chat_message(message, notice_date=""):
    message = (message or "").strip()
    notice_date = (notice_date or "").strip()
    if not message:
        raise AIParseError("消息不能为空。")

    if current_app.config.get("AI_DEMO_MODE") or not current_app.config.get("AI_API_KEY"):
        return _demo_chat(message, notice_date)

    content = _call_chat_model(message, notice_date)
    return parse_chat_model_json(content)


def parse_model_json(content):
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise AIParseError("AI返回内容不是合法JSON，请重试。") from exc
    return validate_ai_payload(payload)


def parse_chat_model_json(content):
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise AIParseError("AI返回内容不是合法JSON，请重试。") from exc
    if not isinstance(payload, dict):
        raise AIParseError("AI返回JSON必须是对象。")

    message_type = payload.get("type")
    reply = payload.get("reply", "")
    if message_type not in ("chat", "task"):
        raise AIParseError("AI返回type必须是chat或task。")
    if not isinstance(reply, str) or not reply.strip():
        raise AIParseError("AI返回reply必须是非空字符串。")

    if message_type == "chat":
        return {"type": "chat", "reply": reply.strip()}

    task_payload = payload.get("task")
    if not isinstance(task_payload, dict):
        raise AIParseError("AI返回task必须是对象。")
    data = validate_ai_payload(task_payload)
    return {
        "type": "task",
        "reply": reply.strip(),
        "result": {
            "data": data,
            "mode": "ai",
            "reply": reply.strip(),
        },
    }


def validate_ai_payload(payload):
    if not isinstance(payload, dict):
        raise AIParseError("AI返回JSON必须是对象。")

    missing = [field for field in AI_FIELDS if field not in payload]
    if missing:
        raise AIParseError(f"AI返回缺少字段：{', '.join(missing)}。")

    data = {}
    text_fields = (
        "course_name",
        "title",
        "task_type",
        "deadline",
        "description",
        "submission_requirements",
        "source_quote",
        "priority",
        "confidence",
    )
    for field in text_fields:
        if not isinstance(payload[field], str):
            raise AIParseError(f"{field} 必须是字符串。")
        data[field] = payload[field].strip()

    for field in ("required_materials", "suggested_materials", "uncertain_fields"):
        value = payload[field]
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise AIParseError(f"{field} 必须是字符串数组。")
        data[field] = "\n".join(item.strip() for item in value if item.strip())

    if not isinstance(payload["estimated_minutes"], int) or payload["estimated_minutes"] < 0:
        raise AIParseError("estimated_minutes 必须是非负整数。")
    data["estimated_minutes"] = payload["estimated_minutes"]

    if data["priority"] not in ("低", "中", "高"):
        raise AIParseError("priority 必须是低、中或高。")
    if data["task_type"] not in ALLOWED_TASK_TYPES:
        raise AIParseError("task_type 不在允许范围内。")
    if data["confidence"] not in ALLOWED_CONFIDENCE:
        raise AIParseError("confidence 不在允许范围内。")
    if data["deadline"]:
        try:
            datetime.strptime(data["deadline"], DATE_FORMAT)
        except ValueError as exc:
            raise AIParseError("deadline 必须为空或符合 YYYY-MM-DDTHH:MM。") from exc

    data["status"] = "未开始"
    data["source_type"] = "AI文字解析"
    return data


def _call_model(notice_text, notice_date):
    api_base_url = current_app.config.get("AI_API_BASE_URL")
    model = current_app.config.get("AI_MODEL")
    if not api_base_url or not model:
        raise AIParseError("AI接口地址或模型未配置，请检查 .env。")

    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是课程任务信息抽取助手。只返回严格JSON对象，不要Markdown。"
                    "截止日期不明确时不要猜测，deadline填空字符串并写入uncertain_fields。"
                    "相对日期只有在用户提供通知日期时才可推算，并将confidence设为AI推断。"
                    "task_type只能是作业、实验、阅读、小论文、考试、项目、其他或空字符串；"
                    "priority只能是低、中、高；confidence只能是原文明确、AI推断或信息缺失。"
                    "required_materials、suggested_materials、uncertain_fields必须是字符串数组。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "notice_date": notice_date,
                        "notice_text": notice_text,
                        "required_fields": AI_FIELDS,
                    },
                    ensure_ascii=False,
                ),
            },
        ],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }
    req = request.Request(
        api_base_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {current_app.config['AI_API_KEY']}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=current_app.config.get("AI_TIMEOUT", 20)) as response:
            response_payload = json.loads(response.read().decode("utf-8"))
    except error.URLError as exc:
        raise AIParseError("AI接口请求失败，请稍后重试。") from exc
    except TimeoutError as exc:
        raise AIParseError("AI接口请求超时，请稍后重试。") from exc
    except json.JSONDecodeError as exc:
        raise AIParseError("AI接口响应不是合法JSON。") from exc

    try:
        return response_payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise AIParseError("AI接口响应缺少模型输出内容。") from exc


def _call_chat_model(message, notice_date):
    api_base_url = current_app.config.get("AI_API_BASE_URL")
    model = current_app.config.get("AI_MODEL")
    if not api_base_url or not model:
        raise AIParseError("AI接口地址或模型未配置，请检查 .env。")

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": CHAT_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "notice_date": notice_date,
                        "message": message,
                        "required_task_fields": AI_FIELDS,
                    },
                    ensure_ascii=False,
                ),
            },
        ],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }
    req = request.Request(
        api_base_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {current_app.config['AI_API_KEY']}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=current_app.config.get("AI_TIMEOUT", 20)) as response:
            response_payload = json.loads(response.read().decode("utf-8"))
    except error.URLError as exc:
        raise AIParseError("AI接口请求失败，请稍后重试。") from exc
    except TimeoutError as exc:
        raise AIParseError("AI接口请求超时，请稍后重试。") from exc
    except json.JSONDecodeError as exc:
        raise AIParseError("AI接口响应不是合法JSON。") from exc

    try:
        return response_payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise AIParseError("AI接口响应缺少模型输出内容。") from exc


def _demo_chat(message, notice_date):
    if not _looks_like_task(message):
        return {
            "type": "chat",
            "reply": "你好，我可以帮你整理课程通知、作业和考试，也可以把确认后的任务用于学习计划。",
        }
    data = _demo_parse(message, notice_date)
    return {
        "type": "task",
        "reply": "我先用本地演示模式整理出一份任务草稿，请你确认。",
        "result": {
            "data": data,
            "mode": "demo",
            "reply": "我先用本地演示模式整理出一份任务草稿，请你确认。",
        },
    }


def _looks_like_task(text):
    task_keywords = (
        "课程",
        "作业",
        "考试",
        "实验",
        "论文",
        "报告",
        "项目",
        "阅读",
        "复习",
        "提交",
        "上传",
        "截止",
        "ddl",
        "deadline",
    )
    return any(keyword in text for keyword in task_keywords)


def _demo_parse(notice_text, notice_date):
    deadline, deadline_inferred = _extract_deadline(notice_text, notice_date)
    uncertain_fields = []
    if not deadline:
        uncertain_fields.append("deadline")

    course_name = _match_first(r"(?:课程|科目)[:：]\s*([^\n，,。；;]+)", notice_text)
    if not course_name:
        uncertain_fields.append("course_name")

    title = _match_first(r"(?:任务|作业|报告|实验)[:：]\s*([^\n。；;]+)", notice_text)
    if not title:
        title = "待确认任务"
        uncertain_fields.append("title")

    task_type = _guess_task_type(notice_text)
    description = notice_text[:500]
    required_materials = _extract_materials(notice_text, "需要")
    suggested_materials = _extract_materials(notice_text, "建议")
    submission_requirements = _extract_submission(notice_text)

    confidence = "原文明确"
    if uncertain_fields:
        confidence = "信息缺失"
    elif deadline_inferred:
        confidence = "AI推断"

    return validate_ai_payload(
        {
            "course_name": course_name,
            "title": title,
            "task_type": task_type,
            "deadline": deadline,
            "description": description,
            "submission_requirements": submission_requirements,
            "required_materials": required_materials,
            "suggested_materials": suggested_materials,
            "estimated_minutes": _extract_estimated_minutes(notice_text),
            "priority": _guess_priority(notice_text),
            "source_quote": _extract_source_quote(notice_text),
            "confidence": "本地演示" if confidence == "原文明确" else confidence,
            "uncertain_fields": uncertain_fields,
        }
    )


def _extract_deadline(text, notice_date):
    full_date = re.search(
        r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})日?\s*(?:([01]?\d|2[0-3])[:：点时](\d{1,2})?)?",
        text,
    )
    if full_date:
        year, month, day, hour, minute = full_date.groups()
        if not hour:
            return "", False
        minute = minute or "00"
        return f"{int(year):04d}-{int(month):02d}-{int(day):02d}T{int(hour):02d}:{int(minute):02d}", False

    relative = re.search(r"下周([一二三四五六日天])\s*(?:([01]?\d|2[0-3])[:：点时](\d{1,2})?)?", text)
    if not relative or not notice_date:
        return "", False
    weekday_text, hour, minute = relative.groups()
    if not hour:
        return "", False
    try:
        notice_dt = datetime.strptime(notice_date, "%Y-%m-%d")
    except ValueError as exc:
        raise AIParseError("通知日期格式无效。") from exc
    weekday_map = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
    next_monday = notice_dt - timedelta(days=notice_dt.weekday()) + timedelta(days=7)
    deadline_dt = next_monday + timedelta(days=weekday_map[weekday_text])
    minute = minute or "00"
    return deadline_dt.replace(hour=int(hour), minute=int(minute)).strftime(DATE_FORMAT), True


def _match_first(pattern, text):
    match = re.search(pattern, text)
    return match.group(1).strip() if match else ""


def _guess_task_type(text):
    for keyword, task_type in (
        ("实验", "实验"),
        ("阅读", "阅读"),
        ("论文", "小论文"),
        ("考试", "考试"),
        ("项目", "项目"),
        ("作业", "作业"),
    ):
        if keyword in text:
            return task_type
    return "其他"


def _extract_submission(text):
    match = re.search(r"([^。；;\n]*(?:提交|上传)[^。；;\n]*)", text)
    return match.group(1).strip() if match else ""


def _extract_materials(text, marker):
    match = re.search(rf"{marker}材料[:：]?\s*([^。；;\n]+)", text)
    if not match:
        return []
    return [item.strip(" 、,，") for item in re.split(r"[、,，]", match.group(1)) if item.strip()]


def _extract_estimated_minutes(text):
    hour = re.search(r"(\d+)\s*小时", text)
    if hour:
        return int(hour.group(1)) * 60
    minute = re.search(r"(\d+)\s*分钟", text)
    return int(minute.group(1)) if minute else 0


def _guess_priority(text):
    if any(keyword in text for keyword in ("重要", "尽快", "期末", "逾期")):
        return "高"
    return "中"


def _extract_source_quote(text):
    for sentence in re.split(r"[。；;\n]", text):
        if "截止" in sentence or "提交" in sentence or "上传" in sentence:
            return sentence.strip()
    return text[:120]

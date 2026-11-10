"""销售助手的 UTF-8 JSON 进程入口。"""

import hashlib
import json
import re
import sys
from datetime import datetime

from .engine import AccountLayeringSkill
from .integrator import CardToPromptIntegrator
from .models import AccountIntelCard, Evidence, RawCollectedData, StaleCardError

_CONTACT = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)|(?<!\d)0\d{2,3}[- ]\d{7,8}(?!\d)")


def _excerpt(value: str) -> str:
    # 选择一段连续原文，跳过联系方式，不把整份采集正文交给生成器。
    return next((part.strip()[:360] for part in _CONTACT.split(value[:2400]) if len(part.strip()) >= 8), "")


def _publication(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return value if datetime.fromisoformat(value).tzinfo else None
    except ValueError:
        return None


def _create(request: dict) -> dict:
    raw = dict(request["raw"])
    events = []
    for source in request["documents"][:20]:
        excerpt = _excerpt(source["text"])
        if not excerpt:
            continue
        events.append({
            "title": _CONTACT.sub("", source["title"]).strip()[:200] or "客户资料",
            "source": "公开采集记录" if source.get("public") else "用户补充资料",
            "source_excerpt": excerpt,
            "url": source.get("url") if AccountLayeringSkill._valid_source_url(source.get("url")) else None,
            "published_at": _publication(source.get("published_at")),
            "category": "collected_document",
        })
    if not events:
        raise ValueError("尚未生成作战档案，请先搜集或补充可用客户资料")
    raw["news_events"] = events
    card = AccountLayeringSkill().process(RawCollectedData.model_validate(raw))
    return {"card": card.model_dump(mode="json")}


def _contexts(request: dict) -> dict:
    card = AccountIntelCard.model_validate(request["card"])
    integrator = CardToPromptIntegrator()
    context = {
        "visit": integrator.to_visit_prep(card),
        "email": integrator.to_email_context(card),
        "demo": integrator.to_demo_invite_context(card),
    }
    whitelist = {}
    for signal in card.layers["signal"].signals:
        if signal.timeliness == "冲突" or signal.polarity in ("否定", "失败"):
            continue
        for evidence in signal.source_evidence:
            if evidence.source_type == "manual" or len(evidence.quote) < 8:
                continue
            if evidence.url and not AccountLayeringSkill._valid_source_url(evidence.url):
                continue
            identity = json.dumps([evidence.url, evidence.source, evidence.quote], ensure_ascii=False)
            key = "ev-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
            whitelist[key] = _citation(key, signal.title, evidence)
    return {"contexts": context, "evidence": list(whitelist.values())}


def _citation(key: str, title: str, evidence: Evidence) -> dict:
    return {"id": key, "title": title, "url": evidence.url or "", "text": evidence.quote,
            "publishedAt": evidence.published_at.isoformat() if evidence.published_at else None,
            "status": "fact" if evidence.source_type == "fact" else "collected_unverified"}


def main() -> None:
    try:
        request = json.loads(sys.stdin.buffer.read().decode("utf-8"))
        handlers = {"create": _create, "contexts": _contexts}
        result = handlers[request["operation"]](request)
        output = {"ok": True, **result}
    except (ValueError, KeyError, TypeError) as exc:
        message = str(exc) if type(exc) in (ValueError, StaleCardError) else "作战档案输入或版本结构无效"
        code = "UNKNOWN_TEMPLATE" if message.startswith(("未知行业", "未知模板", "未知默认模板")) else "INVALID_CARD"
        print(f"sales_skill: {code}", file=sys.stderr)
        output = {"ok": False, "error": {"code": code, "message": message[:400]}}
    except Exception:
        print("sales_skill: INTERNAL_ERROR", file=sys.stderr)
        output = {"ok": False, "error": {"code": "INTERNAL_ERROR", "message": "作战档案处理失败，请检查 Skill 配置"}}
    sys.stdout.buffer.write(json.dumps(output, ensure_ascii=False).encode("utf-8"))


if __name__ == "__main__":
    main()

"""运行：python -m sales_skill.demo。全部资料均为虚构。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from .engine import AccountLayeringSkill
from .integrator import CardToPromptIntegrator
from .models import DecisionNode, Event, JobPost, NewsEvent, PatchRecord, RawCollectedData


def example_data() -> RawCollectedData:
    recent = datetime.now(timezone.utc) - timedelta(days=7)
    return RawCollectedData(
        company_name="华东智造科技有限公司", industry="manufacturing",
        base_info_dict={"business_model": "虚构企业：面向工业客户提供智能制造设备与产线服务", "scale": "约300人（示例数据）"},
        funding_events=[Event(title="完成B轮融资", description="拟用于产线与海外业务拓展", source="虚构融资公告", url="https://example.org/funding", occurred_at=recent, published_at=recent)],
        job_postings=[JobPost(title="海外区域销售经理", description="多个区域招聘", source="虚构招聘页", url="https://example.org/sales", posted_at=recent), JobPost(title="生产合规工程师", description="密集招聘，负责生产标准与追溯", source="虚构招聘页", url="https://example.org/compliance", posted_at=recent)],
        news_events=[NewsEvent(title="新国标Q3执行", description="具体标准编号、年份、生效日期和适用范围均待核实", source="虚构新闻", url="https://example.org/standard", published_at=recent)],
        crm_history={"last_contact_type": "cold_email", "last_contact_at": (recent - timedelta(days=83)).isoformat(), "replied": False, "summary": "3个月前发过一次冷邮件，无后续"},
    )


def main() -> None:
    engine = AccountLayeringSkill()
    card = engine.process(example_data())
    print("=== 初始卡片（全部为虚构资料） ===")
    print(card.model_dump_json(indent=2))
    pain = card.layers["pain"].model_dump(mode="json")["hypotheses"][1]
    corrected = dict(pain)
    corrected.update(
        description="现有产线数据孤岛导致良率分析滞后，无法满足新标准可追溯要求",
        validation_question="产线数据目前如何汇总？良率分析延迟多久，是否影响新标准要求的追溯验证？",
        related_signal_ids=[s.signal_id for s in card.layers["signal"].signals if "国标" in s.title or "合规" in s.title],
        status="人工修正", reasoning="销售根据线下沟通修正，仍须通过问题验证，未标记为事实",
    )
    card = engine.apply_patch(card, PatchRecord(
        target_layer="pain", field_path="/hypotheses/1", original_value=pain,
        corrected_value=corrected, reason="销售判断成本优化不匹配实际，应验证产线数据与追溯问题",
    ))
    node = DecisionNode(node_id="node-production-director", role="生产总监", decision_role="关键使用者", heat="高", is_inferred=False, relationship_note="销售人工补充角色；姓名与关系温度仍未知")
    card = engine.apply_patch(card, PatchRecord(
        target_layer="network", field_path="/decision_heatmap/-", operation="add", original_value=None,
        corrected_value=node.model_dump(mode="json"), reason="销售确认生产总监应作为关键使用者参与验证",
    ))
    print("\n=== 修正后卡片 ===")
    print(card.model_dump_json(indent=2))
    integrator = CardToPromptIntegrator()
    for context in (integrator.to_visit_prep(card), integrator.to_email_context(card), integrator.to_demo_invite_context(card)):
        print("\n" + context)


if __name__ == "__main__":
    main()

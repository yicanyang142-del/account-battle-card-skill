"""第一阶段回归用例，覆盖证据资格与派生内容失效。"""

import unittest
from datetime import datetime, timedelta, timezone

from sales_skill import AccountLayeringSkill, CardToPromptIntegrator, PatchRecord, RawCollectedData, StaleCardError
from sales_skill.models import AccountIntelCard, Event, NewsEvent


class EvidenceRegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = AccountLayeringSkill()
        self.integrator = CardToPromptIntegrator()
        self.now = datetime.now(timezone.utc)

    def _card(self, industry: str = "manufacturing", **data) -> AccountIntelCard:
        return self.engine.process(RawCollectedData(company_name="回归测试企业", industry=industry, **data))

    def test_signal_title_change_refreshes_contexts(self) -> None:
        card = self._card(news_events=[NewsEvent(
            title="公布新国标执行安排", source="示例公告",
            url="https://example.org/standard", published_at=self.now,
        )])
        self.assertIn("公布新国标执行安排", self.integrator.to_email_context(card))
        patched = self.engine.apply_patch(card, PatchRecord(
            target_layer="signal", field_path="/signals/0/title",
            original_value="公布新国标执行安排", corrected_value="已核实标准适用范围变更",
            reason="以更新公告为准",
        ))
        visit = self.integrator.to_visit_prep(patched)
        email = self.integrator.to_email_context(patched)
        demo = self.integrator.to_demo_invite_context(patched)
        for context in (visit, email, demo):
            self.assertNotIn("公布新国标执行安排", context)
        for context in (visit, email):
            self.assertIn("已核实标准适用范围变更", context)

    def test_role_change_refreshes_contexts(self) -> None:
        card = self._card(crm_history={"contacts": [{"role": "业务负责人", "name": "测试联系人"}]})
        self.assertIn("建议先向业务负责人", self.integrator.to_email_context(card))
        patched = self.engine.apply_patch(card, PatchRecord(
            target_layer="network", field_path="/decision_heatmap/0/role",
            original_value="业务负责人", corrected_value="区域运营负责人", reason="已核对实际职责",
        ))
        for context in (self.integrator.to_visit_prep(patched), self.integrator.to_email_context(patched), self.integrator.to_demo_invite_context(patched)):
            self.assertNotIn("业务负责人", context)
            self.assertIn("区域运营负责人", context)

    def test_bypassing_patch_cannot_send_stale_content(self) -> None:
        card = self._card()
        card.layers["network"].decision_heatmap[0].role = "直接编辑的岗位"
        with self.assertRaises(StaleCardError):
            self.integrator.to_email_context(card)

    def test_stage_change_refreshes_visit_context(self) -> None:
        card = self._card()
        original_stage = card.layers["base"].stage
        self.assertIn("公司阶段待核实", self.integrator.to_visit_prep(card))
        patched = self.engine.apply_patch(card, PatchRecord(
            target_layer="base", field_path="/stage", original_value=original_stage,
            corrected_value="转型", reason="已核对项目方向",
        ))
        for context in (self.integrator.to_visit_prep(patched), self.integrator.to_email_context(patched), self.integrator.to_demo_invite_context(patched)):
            self.assertIn("转型", context)
            self.assertNotIn("公司阶段待核实", context)

    def test_chinese_industry_labels_select_same_templates(self) -> None:
        for label, key in (("智能制造", "manufacturing"), ("企业服务", "saas")):
            with self.subTest(label=label):
                self.assertEqual(self._card(label).layers["pain"], self._card(key).layers["pain"])

    def test_pain_questions_follow_signal_evidence(self) -> None:
        def visit(title: str) -> AccountIntelCard:
            return self._card(news_events=[NewsEvent(
                title=title, source="示例公告", url="https://example.org/news", published_at=self.now,
            )])

        compliance = visit("公开新国标执行安排")
        market = visit("公布海外市场计划")
        self.assertNotEqual(
            [p.validation_question for p in compliance.layers["pain"].hypotheses],
            [p.validation_question for p in market.layers["pain"].hypotheses],
        )

    def test_missing_contacts_never_invents_relationships(self) -> None:
        network = self._card().layers["network"]
        self.assertEqual(network.account_relationship_temperature, "未知")
        for node in network.decision_heatmap:
            self.assertIsNone(node.name)
            self.assertTrue(node.is_inferred)
            self.assertEqual(node.relation_temperature, "未知")
            self.assertEqual(node.heat, "未知")

    def test_failed_or_expired_funding_does_not_set_stage(self) -> None:
        for title, days in (("未完成融资，项目终止", 7), ("完成融资，拟用于扩产", 1500)):
            with self.subTest(title=title):
                card = self._card(funding_events=[Event(
                    title=title, source="示例公告", url="https://example.org/funding",
                    published_at=self.now - timedelta(days=days),
                )])
                self.assertEqual(card.layers["base"].stage, "unknown")

    def test_missing_or_invalid_source_makes_urgency_unknown(self) -> None:
        for source, url in (("", None), ("示例公告", "file:///local.txt")):
            with self.subTest(url=url):
                card = self._card(news_events=[NewsEvent(
                    title="新国标执行公告", source=source, url=url, published_at=self.now,
                )])
                self.assertEqual(card.layers["signal"].signals[0].urgency, "unknown")
                self.assertIn('"urgency": "待核实"', self.integrator.to_email_context(card))

    def test_future_and_conflicting_publications_do_not_drive_stage(self) -> None:
        for events in (
            [Event(title="完成融资，用于扩产", source="公告", url="https://example.org/future",
                   published_at=self.now + timedelta(days=1))],
            [Event(title=title, source="公告", url=f"https://example.org/{i}", published_at=self.now,
                   claim_key="financing-1") for i, title in enumerate(("完成融资，用于扩产", "融资失败"))],
        ):
            with self.subTest(events=events):
                card = self._card(funding_events=events)
                self.assertEqual(card.layers["base"].stage, "unknown")
                self.assertTrue(all(s.urgency == "unknown" for s in card.layers["signal"].signals))
                self.assertIn('"stage": "待核实"', self.integrator.to_demo_invite_context(card))

    def test_recent_supported_expansion_with_deadline_still_qualifies(self) -> None:
        card = self._card(funding_events=[Event(
            title="完成融资，用于扩产", source="公告", url="https://example.org/expansion",
            published_at=self.now, deadline_at=self.now + timedelta(days=10),
        )])
        self.assertEqual(card.layers["base"].stage, "扩张")
        self.assertEqual(card.layers["signal"].signals[0].urgency, "高")


if __name__ == "__main__":
    unittest.main()

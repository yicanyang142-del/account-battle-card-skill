from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError
from sales_skill.demo import example_data
from sales_skill.engine import AccountLayeringSkill
from sales_skill.integrator import CardToPromptIntegrator
from sales_skill.models import AccountIntelCard, DecisionNode, PatchRecord, RawCollectedData


class SkillTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = AccountLayeringSkill()
        self.card = self.engine.process(example_data())

    def test_templates(self) -> None:
        for key in ("manufacturing", "saas"):
            template = self.engine.load_template(key)
            self.assertEqual(template.industry_key, key)
            self.assertGreaterEqual(len(template.pain_library), 3)
        with self.assertRaises(ValueError):
            self.engine.load_template("../secret")
        with self.assertRaises(FileNotFoundError):
            self.engine.load_template("unknown")

    def test_custom_template_directory(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            template = self.engine.load_template("saas")
            Path(folder, "saas.json").write_text(template.model_dump_json(), encoding="utf-8")
            self.assertEqual(AccountLayeringSkill(folder).load_template("saas"), template)

    def test_five_layers_and_json_roundtrip(self) -> None:
        self.assertEqual(set(self.card.layers), {"base", "signal", "network", "pain", "hook"})
        self.assertEqual(self.card.layers["base"].stage, "扩张")
        self.assertEqual(len(self.card.layers["pain"].hypotheses), 3)
        self.assertTrue(all(s.source_evidence for s in self.card.layers["signal"].signals))
        self.assertEqual(AccountIntelCard.model_validate_json(self.card.model_dump_json()), self.card)
        bad = self.card.model_dump()
        bad["layers"].pop("base")
        with self.assertRaises(ValidationError):
            AccountIntelCard.model_validate(bad)

    def test_industry_selection_and_no_evidence(self) -> None:
        card = self.engine.process(RawCollectedData(company_name="空白企业", industry="saas"))
        self.assertTrue(all(p.template_id.startswith("s-") for p in card.layers["pain"].hypotheses))
        self.assertTrue(all(not p.related_signal_ids for p in card.layers["pain"].hypotheses))
        self.assertFalse(card.layers["signal"].signals)
        self.assertIn("暂无", card.layers["pain"].hypotheses[0].reasoning)
        self.assertEqual(card.layers["network"].account_relationship_temperature, "未知")
        overridden = self.engine.process(example_data(), template_key="saas")
        self.assertTrue(all(p.template_id.startswith("s-") for p in overridden.layers["pain"].hypotheses))

    def test_pain_matches_sources_and_crm_does_not_invent_relationship(self) -> None:
        pains = self.card.layers["pain"].hypotheses
        self.assertEqual(pains[1].template_id, "m-cost")
        self.assertTrue(all(p.related_signal_ids for p in pains))
        network = self.card.layers["network"]
        self.assertEqual(network.account_relationship_temperature, "冷")
        self.assertTrue(all(n.name is None and n.relation_temperature == "未知" for n in network.decision_heatmap))

    def test_unknown_date_not_urgent(self) -> None:
        raw = example_data()
        for e in raw.news_events:
            e.published_at = None
        card = self.engine.process(raw)
        signal = next(s for s in card.layers["signal"].signals if "Q3" in s.title)
        self.assertEqual(signal.urgency, "unknown")
        self.assertIn("待核实", signal.urgency_reason)

    def test_immutable_patch_and_history(self) -> None:
        original = self.card.model_dump(mode="json")
        patch = PatchRecord(target_layer="hook", field_path="/opening_angle", original_value=self.card.layers["hook"].opening_angle, corrected_value="人工调整开场问题", reason="现场确认")
        result = self.engine.apply_patch(self.card, patch)
        self.assertEqual(self.card.model_dump(mode="json"), original)
        self.assertEqual(result.version, "1.0.1")
        self.assertEqual(result.patches_applied[0].original_value, patch.original_value)
        self.assertEqual(result.patches_applied[0].reason, "现场确认")
        result.layers["hook"].opening_angle = "再次修改"
        self.assertNotEqual(self.card.layers["hook"].opening_angle, "再次修改")

    def test_remove_pain_and_add_network(self) -> None:
        old = self.card.layers["pain"].hypotheses[1].model_dump(mode="json")
        result = self.engine.apply_patch(self.card, PatchRecord(target_layer="pain", field_path="/hypotheses/1", operation="remove", original_value=old, corrected_value=None, reason="已排除"))
        self.assertEqual(len(result.layers["pain"].hypotheses), 2)
        node = DecisionNode(node_id="production", role="生产总监", decision_role="关键使用者", heat="高")
        result = self.engine.apply_patch(result, PatchRecord(target_layer="network", field_path="/decision_heatmap/-", operation="add", original_value=None, corrected_value=node.model_dump(mode="json"), reason="销售补充"))
        self.assertEqual(result.layers["network"].decision_heatmap[-1].heat, "高")
        self.assertEqual(result.version, "1.0.2")

    def test_invalid_patches_are_atomic(self) -> None:
        before = self.card.model_dump(mode="json")
        for field, old, new in (("/stage", "错误原值", "扩张"), ("/stage", "扩张", "乱填"), ("/missing", None, "x")):
            with self.assertRaises(ValueError):
                self.engine.apply_patch(self.card, PatchRecord(target_layer="base", field_path=field, original_value=old, corrected_value=new, reason="验证失败行为"))
        self.assertEqual(self.card.model_dump(mode="json"), before)
        signal = self.card.layers["signal"].signals[0].model_dump(mode="json")
        updated = self.engine.apply_patch(self.card, PatchRecord(target_layer="signal", field_path="/signals/0", operation="remove", original_value=signal, corrected_value=None, reason="原信号已撤回"))
        self.assertNotIn(signal["signal_id"], updated.layers["hook"].related_signal_ids)
        self.assertTrue(all(signal["signal_id"] not in p.related_signal_ids for p in updated.layers["pain"].hypotheses))

    def test_whole_layer_duplicate_id_and_bad_index(self) -> None:
        base = self.card.layers["base"].model_dump(mode="json")
        patch = PatchRecord(target_layer="base", field_path="", original_value=base, corrected_value={**base, "scale": "人工确认500人"}, reason="线下确认")
        result = self.engine.apply_patch(self.card, patch)
        self.assertEqual(result.layers["base"].scale, "人工确认500人")
        with self.assertRaises(ValueError):
            self.engine.apply_patch(result, patch)
        for index in ("-1", "99", "01"):
            with self.assertRaises(ValueError):
                self.engine.apply_patch(self.card, PatchRecord(target_layer="pain", field_path=f"/hypotheses/{index}", original_value=None, corrected_value=None, reason="非法索引"))

    def test_integrator_uses_corrected_layers_not_history(self) -> None:
        old = self.card.layers["pain"].hypotheses[1].description
        result = self.engine.apply_patch(self.card, PatchRecord(target_layer="pain", field_path="/hypotheses/1/description", original_value=old, corrected_value="数据孤岛待验证", reason="销售修正"))
        integrator = CardToPromptIntegrator()
        visit = integrator.to_visit_prep(result)
        email = integrator.to_email_context(result)
        demo = integrator.to_demo_invite_context(result)
        self.assertIn("痛点假设", visit)
        self.assertIn("话术锚点", email)
        self.assertIn("决策链", demo)
        self.assertIn("关系温度", email)
        self.assertIn("数据孤岛待验证", visit)
        self.assertNotIn(old, visit)
        self.assertIn("1.0.1", demo)


if __name__ == "__main__":
    unittest.main()

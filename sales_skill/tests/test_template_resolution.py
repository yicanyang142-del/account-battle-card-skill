"""阶段二验收用例，验证模板选择顺序和配置错误。"""

import json
import tempfile
import unittest
from pathlib import Path

from sales_skill import AccountLayeringSkill, RawCollectedData


class TemplateResolutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = AccountLayeringSkill()

    def test_explicit_manufacturing_overrides_saas(self) -> None:
        raw = RawCollectedData(company_name="样例企业", industry="saas")
        card = self.engine.process(raw, template_key="manufacturing")
        self.assertEqual(card.template_key, "manufacturing")
        self.assertTrue(all(p.template_id.startswith("m-") for p in card.layers["pain"].hypotheses))

    def test_industry_alias_used_when_no_explicit_override(self) -> None:
        raw = RawCollectedData(company_name="样例企业", industry=" 企业服务 ")
        self.assertEqual(self.engine.process(raw).template_key, "saas")

    def test_missing_industry_uses_configured_default(self) -> None:
        raw = RawCollectedData(company_name="样例企业")
        engine = AccountLayeringSkill(default_template_key="saas")
        self.assertEqual(engine.process(raw).template_key, "saas")

    def test_unknown_nonempty_industry_does_not_use_default(self) -> None:
        raw = RawCollectedData(company_name="样例企业", industry="retail")
        with self.assertRaisesRegex(ValueError, "未知行业.*retail"):
            self.engine.process(raw)

    def test_unknown_explicit_key_does_not_use_known_industry(self) -> None:
        raw = RawCollectedData(company_name="样例企业", industry="saas")
        with self.assertRaisesRegex(ValueError, "未知模板.*retail"):
            self.engine.process(raw, template_key="retail")

    def test_custom_template_errors_explain_the_failed_step(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            engine = AccountLayeringSkill(folder)
            template = Path(folder, "sample.json")
            with self.assertRaisesRegex(FileNotFoundError, "模板文件不存在.*sample"):
                engine.load_template("sample")
            template.write_text("{broken", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "模板 JSON 无效.*sample"):
                engine.load_template("sample")
            template.write_text(json.dumps({"industry_key": "sample", "signal_patterns": []}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "模板字段无效.*sample") as failure:
                engine.load_template("sample")
            self.assertIn("signal_patterns", str(failure.exception))

# Account Battle-Card Layering Skill

本模块应被插入在**数据搜集模块之后、内容生成模块之前**。它是可导入的Python业务模块，不依赖Codex运行时或LLM，无联网、邮件发送或数据库副作用。Python 3.11+，唯一第三方依赖为Pydantic v2。

```sh
python -m pip install "pydantic>=2,<3"
python -m sales_skill.demo
python -m unittest discover -s sales_skill/tests -v
# 只运行第一阶段回归用例
python -m unittest sales_skill.tests.test_evidence_regressions -v
# 只运行第二阶段模板解析用例
python -m unittest sales_skill.tests.test_template_resolution -v
```

从包含sales_skill目录的父目录运行命令。目录同时提供需求中列出的init.py和Python真正使用的__init__.py。
`sales_skill.tests` 是模块路径，原 `sales_skill/tests/test_skill.py` 没有移动。发现模式会同时执行原测试与阶段回归测试。

## 插入现有链路

```python
from sales_skill import AccountLayeringSkill, RawCollectedData, CardToPromptIntegrator

raw = RawCollectedData.model_validate_json(collected_json)
engine = AccountLayeringSkill()
card = engine.process(raw)
# 人工修正时：card = engine.apply_patch(card, validated_patch)
integrator = CardToPromptIntegrator()
visit_context = integrator.to_visit_prep(card)
email_context = integrator.to_email_context(card)
demo_context = integrator.to_demo_invite_context(card)
# 将各context拼接到对应生成器的指令与产品知识上下文中。
```

若搜集模块已输出dict，使用RawCollectedData.model_validate(payload)。生成器继续负责内容生成、产品能力校验和邮件预览确认；本模块不执行发送。现有Node.js工具可调用Python子进程或内部Python服务传递JSON；本实现不擅自改变现有工具或客户数据。

## 输入约定与五层规则

公司名称必填，`industry` 可省略，列表默认空。`base_info_dict` 支持 `business_model`、`scale`。Event/NewsEvent 支持标题、描述、来源、URL 和带时区的事件日期。JobPost 支持标题、描述、部门、来源、URL 和带时区的发布日期。生产采集器应补全真实出处。缺少来源会显示“来源未提供”。

CRM可传入last_contact_type="cold_email"、replied=false、summary、relationship_temperature，以及contacts=[{"role":"技术负责人","name":"真实已知姓名","relationship_temperature":"温","note":"关系依据"}]。客户整体冷关系不会自动映射给所有角色；模板角色为推断，未提供姓名时不会编造联系人。heat表示决策影响热力，与关系温度独立。当前组织变动作为信号沉淀，具名角色须由结构化CRM或人工补充，避免从自然语言误认负责人。

阶段和紧迫度可以为 `unknown`，下游显示“待核实”。事件的 `published_at` 与 `occurred_at` 分开，日期需要时区。阶段使用来源和日期合格的正向证据。模板可配置 `evidence_window_days`（融资默认365天，新闻180天）和 `urgent_deadline_days`（默认30天）。高紧迫度需要明确的 `deadline_at`。同 `claim_key` 的相反记录按冲突处理。URL 校验检查格式和明显的本机或内网地址，来源核验依赖采集端提供的记录。

痛点基于模板关键词和有效信号选择三条，按模板顺序输出，并把相关信号带入验证问题。无匹配时标明仅为行业访谈假设。支持人工删除后少于三条。`apply_patch` 返回前重算相关派生层，并刷新依赖快照。信号变化会重算自动痛点和话术，岗位或阶段变化会重算话术。被重算的派生字段原有人工修改仍保存在修正历史中。上下文方法在发现依赖快照缺失或不一致时抛出 `StaleCardError`。旧卡片可读取，生成上下文前需要用原始输入重建并复核历史修正。

## 人工修正

PatchRecord包含patch_id、target_layer、field_path、original_value、corrected_value、reason、patched_at及operation（默认replace）。field_path采用相对该层的JSON Pointer：/hypotheses/1/description；数组追加使用/decision_heatmap/-；空串表示替换整层。remove必须corrected_value=null；add必须original_value=null，且对象目标字段不能已存在。

apply_patch先比较原值，再深拷贝更新并重新校验五层和信号/角色引用。原卡片不变；保留原值、修正原因与时间，版本末位递增。无效路径、无效枚举、过期原值、重复patch_id、悬空引用均拒绝。原值使用卡片model_dump(mode="json")中的JSON表示。卡片可通过model_dump_json持久化，再通过AccountIntelCard.model_validate_json恢复；存储与并发版本检查由宿主负责。

## 配置行业模板

内置 `manufacturing` 和 `saas`，支持“智能制造”和“企业服务”两个中文别名。`process(raw_data, template_key=None)` 按显式模板、非空行业、配置默认值的顺序选择。通过 `AccountLayeringSkill(default_template_key="saas")` 可配置默认模板，默认是 `manufacturing`。公司未提供行业时，卡片记录选用的模板，行业标签保持为空。

自定义目录通过 `AccountLayeringSkill(template_path="/path/to/templates")` 提供。`process()` 对未知行业或模板抛出带键名的 `ValueError`。直接调用 `load_template()` 时，文件缺失抛出 `FileNotFoundError`。无效 JSON 和模板字段错误分别通过 `ValueError` 报告。

扩展时新增<industry_key>.json，包含industry_key、signal_patterns、pain_library（至少3条，唯一template_id、description、validation_question、signal_keywords）、role_map（岗位→决策角色）和hook_strategies。扩展无需修改引擎。为让切入角度选择到对应角色，决策角色可使用“关键影响者”“关键使用者”“合规影响者”等内置名称。

## 文件树

```text
sales_skill/
  __init__.py
  init.py
  models.py
  engine.py
  integrator.py
  demo.py
  README.md
  templates/
    __init__.py
    init.py
    manufacturing.json
    saas.json
  tests/
    __init__.py
    test_skill.py
    test_evidence_regressions.py
    test_template_resolution.py
```

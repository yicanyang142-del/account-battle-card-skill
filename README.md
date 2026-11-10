# 客户作战档案 Skill | Account Battle Card Skill

把搜集到的客户资料整理为五层作战档案，为 B2B 销售提供拜访准备、邮件草稿和演示邀约所需的上下文。

**资料 JSON → 基础信息 → 触发信号 → 决策链 → 痛点假设 → 话术锚点 → 三种生成器上下文**

- [虚构客户输入](examples/customer.json)
- [初始档案、人工修正和三种下游上下文](examples/demo-output.txt)
- [核心引擎](sales_skill/engine.py) · [证据与修正回归测试](sales_skill/tests/test_evidence_regressions.py)
- [接入方式、字段约定和行业模板](sales_skill/README.md)

使用 Python 3.11+。示例和测试在本地运行，无需 API Key。示例中的公司、联系人线索和来源均为虚构。

```sh
python -m pip install -r requirements.txt
python -m sales_skill.demo
python -m unittest discover -s sales_skill/tests -v
```

## 两个回归案例

- **证据不足。** 缺少合格来源或日期的信号不能支撑阶段判断。没有 CRM 联系人时，岗位可作为待验证线索，姓名和关系温度保持未知。
- **修正后重新生成。** 修改信号、岗位或阶段后，引擎更新相关派生内容。若卡片的依赖记录已失效，上下文转换会报错。

档案中的痛点是销售访谈假设。来源 URL 通过格式校验后，来源真实性仍由采集方核实。模块输出上下文，由宿主应用生成最终文案并负责发送。

# Account Battle Card Skill｜客户作战档案

这是一个可复用的 B2B 销售客户情报模块：把搜集到的公司资料整理成结构化作战档案，并输出拜访准备、邮件草稿和演示邀约所需的上下文。

## 解决什么问题

销售在触达客户前，需要把零散的公司信息、新闻、融资、招聘和 CRM 记录重新整理一遍。这个 Skill 把这个过程固化成固定流程，输出可追溯、可人工修正、可继续生成文案的客户档案。

## 核心流程

`资料 JSON → 基础信息 → 触发信号 → 决策链 → 痛点假设 → 话术锚点 → 拜访 / 邮件 / 演示上下文`

## 输入与输出

### 输入

- 公司名称
- 行业
- 基础信息
- 新闻、融资、招聘、技术线索
- CRM 联系人与历史记录

### 输出

- `AccountIntelCard`：结构化客户作战档案
- 拜访准备上下文
- 邮件生成上下文
- 演示邀约上下文

## 运行方式

```sh
python -m pip install -r requirements.txt
python -m sales_skill.demo
python -m unittest discover -s sales_skill/tests -v
```

## 边界与约束

- 痛点默认是待验证假设，不是事实。
- 缺少来源、日期或合格证据的信号不支撑阶段判断。
- 没有 CRM 联系人时，不补写姓名或关系温度。
- 模块只输出上下文，不负责最终文案生成或邮件发送。

## 项目文件

- [`examples/customer.json`](examples/customer.json)：虚构客户输入
- [`examples/demo-output.txt`](examples/demo-output.txt)：初始档案与三类下游上下文
- [`sales_skill/engine.py`](sales_skill/engine.py)：核心分层与修正引擎
- [`sales_skill/README.md`](sales_skill/README.md)：字段约定和行业模板
- [`sales_skill/tests/`](sales_skill/tests)：回归测试

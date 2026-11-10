"""确定性分层规则引擎：不请求外部模型，不将行业常识冒充客户事实。"""
from __future__ import annotations

import copy
import json
import re
from datetime import datetime, timezone
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import urlsplit
from typing import Any

from pydantic import TypeAdapter, ValidationError

from .models import (
    AccountIntelCard, BaseLayer, DecisionNode, Evidence, HookLayer,
    Event, IndustryTemplate, LayerKey, LayerModel, NetworkLayer, PainHypothesis,
    PainPointLayer, PatchRecord, RawCollectedData, Signal, SignalLayer, StaleCardError,
)


class AccountLayeringSkill:
    def __init__(
        self, template_path: str | Path | None = None, *, default_template_key: str = "manufacturing",
    ) -> None:
        self.template_path = Path(template_path) if template_path else Path(__file__).parent / "templates"
        self.default_template_key = default_template_key

    def load_template(self, template_key: str) -> IndustryTemplate:
        template_key = self._industry_key(template_key)
        if not re.fullmatch(r"[a-z][a-z0-9_]*", template_key):
            raise ValueError(f"无效行业模板键 {template_key!r}")
        path = self.template_path / f"{template_key}.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise FileNotFoundError(f"模板文件不存在 {template_key!r}（{path}）") from exc
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError(f"模板 JSON 无效 {template_key!r}（{exc}）") from exc
        try:
            template = IndustryTemplate.model_validate(data)
        except ValidationError as exc:
            raise ValueError(f"模板字段无效 {template_key!r}\n{exc}") from exc
        if template.industry_key != template_key:
            raise ValueError(f"模板字段无效 {template_key!r}，industry_key 与文件名不一致")
        return template

    def process(self, raw_data: RawCollectedData, template_key: str | None = None) -> AccountIntelCard:
        template = self._resolve_template(raw_data.industry, template_key)
        card = self.compile_card(raw_data, self.analyze_layers(raw_data, template))
        return card.model_copy(update={"template_key": template.industry_key})

    def _resolve_template(self, industry: str, template_key: str | None) -> IndustryTemplate:
        if template_key is not None:
            selected, label = template_key, "未知模板"
        elif industry.strip():
            selected, label = industry, "未知行业"
        else:
            selected, label = self.default_template_key, "未知默认模板"
        key = self._industry_key(selected)
        if not re.fullmatch(r"[a-z][a-z0-9_]*", key):
            raise ValueError(f"{label} {selected!r}")
        try:
            return self.load_template(key)
        except FileNotFoundError as exc:
            raise ValueError(f"{label} {selected!r}，找不到对应模板 {key!r}") from exc

    def analyze_layers(self, raw_data: RawCollectedData, template: IndustryTemplate) -> dict[LayerKey, LayerModel]:
        signal = self._build_signals(raw_data, template)
        base = self._build_base(raw_data, signal)
        network = self._build_network(raw_data, template)
        pain = self._build_pain(signal, template)
        hook = self._build_hook(base, signal, network, pain, template)
        return {"base": base, "signal": signal, "network": network, "pain": pain, "hook": hook}

    def compile_card(self, raw_data: RawCollectedData, layers: dict[LayerKey, LayerModel]) -> AccountIntelCard:
        card = AccountIntelCard(company_name=raw_data.company_name, layers=layers)
        return self._stamp_dependencies(card)

    @staticmethod
    def _industry_key(key: str) -> str:
        key = key.strip().casefold()
        return {"智能制造": "manufacturing", "企业服务": "saas"}.get(key, key)

    @staticmethod
    def _stamp_dependencies(card: AccountIntelCard) -> AccountIntelCard:
        return card.model_copy(update={
            "derived_dependencies": {key: card.dependency_snapshot(key) for key in ("pain", "hook")},
        })

    @staticmethod
    def _stage_from_signals(signals: SignalLayer) -> tuple[str, str, list[Evidence]]:
        candidates: dict[str, list[Evidence]] = {}
        for signal in signals.signals:
            if signal.timeliness != "有效" or signal.polarity != "正面" or signal.category == "招聘":
                continue
            text = " ".join(e.quote for e in signal.source_evidence)
            stages = (
                ("扩张", r"扩产|新增产线|进入新市场|海外业务拓展"),
                ("优化", r"启动降本|开展流程优化|实施提效"),
                ("转型", r"启动转型|实施业务重组|启动战略转型"),
                ("防御", r"实施裁员|缩减产能|退出市场|关闭工厂"),
            )
            for stage, pattern in stages:
                if re.search(pattern, text):
                    if stage == "扩张" and signal.category == "融资" and not re.search(r"完成.*融资|融资.*完成|融资到账", text):
                        continue
                    candidates.setdefault(stage, []).extend(signal.source_evidence)
        if len(candidates) != 1:
            return "unknown", "缺少一致且有效的阶段依据，待核实", []
        stage, evidence = next(iter(candidates.items()))
        return stage, "有效采集记录支持这一阶段推断，仍待核实", evidence

    def _build_base(self, raw: RawCollectedData, signals: SignalLayer) -> BaseLayer:
        stage, reason, evidence = self._stage_from_signals(signals)
        return BaseLayer(
            stage=stage, stage_reason=reason,
            business_model_summary=str(raw.base_info_dict.get("business_model", "业务模式未提供，待确认")),
            scale=str(raw.base_info_dict.get("scale", "规模未提供")),
            industry_tags=[raw.industry.strip()] if raw.industry.strip() else [], source_evidence=evidence,
        )

    @staticmethod
    def _valid_source_url(url: str | None) -> bool:
        if not url or any(ch.isspace() for ch in url):
            return False
        try:
            parsed = urlsplit(url)
            host = (parsed.hostname or "").rstrip(".")
            if parsed.scheme not in ("http", "https") or parsed.username is not None or parsed.password is not None:
                return False
            _ = parsed.port  # 访问属性会校验端口。
            try:
                address = ip_address(host)
                return address.is_global and not address.is_multicast
            except ValueError:
                if host.lower().endswith((".local", ".localhost", ".internal")):
                    return False
                return bool(re.fullmatch(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{1,62}", host, re.I))
        except ValueError:
            return False

    def _build_signals(self, raw: RawCollectedData, template: IndustryTemplate) -> SignalLayer:
        signals: list[Signal] = []
        today = datetime.now(timezone.utc).date()

        def add(category: str, title: str, evidence: Evidence, event: Event | None = None) -> None:
            text = evidence.quote
            polarity = event.polarity if event else "unknown"
            if re.search(r"未完成|失败|终止|取消|撤回", text):
                polarity = "失败"
            elif re.search(r"不适用|并未|尚未|未启动|不涉及|否认|没有|暂无|尚无|不会|不再|无计划", text):
                polarity = "否定"
            elif polarity == "unknown":
                polarity = "正面"
            published = evidence.published_at
            age = (today - published.astimezone(timezone.utc).date()).days if published else None
            occurred = evidence.occurred_at
            historical_age = (today - occurred.astimezone(timezone.utc).date()).days if occurred else None
            if age is None or age < 0 or not evidence.source.strip() or not self._valid_source_url(evidence.url):
                timeliness, urgency, why = "unknown", "unknown", "来源或发布日期待核实"
            elif max(age, historical_age or 0) > template.evidence_window_days.get(category, 180):
                timeliness, urgency, why = "过期", "低", "仅作历史参考"
            elif polarity != "正面":
                timeliness, urgency, why = "有效", "低", "否定或失败记录不支持原计划"
            else:
                deadline = event.deadline_at if event else None
                days = (deadline.astimezone(timezone.utc).date() - today).days if deadline else None
                if days is not None and 0 <= days <= template.urgent_deadline_days:
                    timeliness, urgency, why = "有效", "高", "存在明确的临近行动期限"
                else:
                    timeliness, urgency, why = "有效", "中", "近期记录，行动优先级待核实"
            if timeliness == "有效":
                evidence = evidence.model_copy(update={"source_type": "fact"})
            signals.append(Signal(
                signal_id=f"signal-{len(signals) + 1}", category=category, title=title,
                inference="业务影响待验证，不能仅凭此记录确认客户需求",
                urgency=urgency, urgency_reason=why, source_evidence=[evidence],
                polarity=polarity, timeliness=timeliness, claim_key=event.claim_key if event else None,
            ))

        for category, events in (("融资", raw.funding_events), ("新闻", raw.news_events), ("组织变动", raw.leadership_changes)):
            for event in events:
                collected = getattr(event, "category", None) == "collected_document"
                if collected or category != "新闻" or any(k in event.title + event.description for k in template.signal_patterns):
                    excerpt = getattr(event, "source_excerpt", None)
                    add(category, event.title, Evidence(source=event.source, quote=excerpt or (event.title + " " + event.description).strip(),
                        url=event.url, occurred_at=event.occurred_at, published_at=event.published_at), event)
        for job in raw.job_postings:
            add("招聘", "招聘：" + job.title, Evidence(source=job.source, quote=(job.title + " " + job.description).strip(),
                url=job.url, occurred_at=job.posted_at, published_at=job.posted_at))
        for tech in raw.tech_indicators:
            add("技术栈", "技术栈线索：" + tech, Evidence(source="tech_indicators（来源待核实）", quote=tech))
        claims: dict[str, list[Signal]] = {}
        for signal in signals:
            if signal.claim_key and signal.timeliness == "有效":
                claims.setdefault(signal.claim_key, []).append(signal)
        for group in claims.values():
            polarities = {s.polarity for s in group}
            if "正面" in polarities and len(polarities) > 1:
                for signal in group:
                    signal.timeliness = "冲突"
                    signal.urgency = "unknown"
                    signal.urgency_reason = "来源对同一事项有冲突，待核实"
                    for evidence in signal.source_evidence:
                        evidence.source_type = "inference"
        return SignalLayer(signals=signals)

    def _build_network(self, raw: RawCollectedData, template: IndustryTemplate) -> NetworkLayer:
        crm = raw.crm_history or {}
        # CRM是宽松输入；只读取明确定义的关系字段，不从冷邮件推断真实姓名或热关系。
        account_temp = crm.get("relationship_temperature", "未知")
        if account_temp not in ("热", "温", "冷", "未知"):
            account_temp = "未知"
        no_response = crm.get("last_contact_type") == "cold_email" and crm.get("replied") is False
        if no_response:
            account_temp = "冷"
        summary = "曾发冷邮件且无回复；未建立有效关系" if no_response else str(crm.get("summary", "无明确关系记录"))
        contacts = crm.get("contacts", [])
        if not isinstance(contacts, list):
            contacts = []
        nodes: list[DecisionNode] = []
        roles = dict(template.role_map)
        for contact in contacts:
            if isinstance(contact, dict) and isinstance(contact.get("role"), str) and contact["role"].strip():
                roles.setdefault(contact["role"], "决策职责待核实")
        for i, (role, mapped) in enumerate(roles.items()):
            contact = next((c for c in contacts if isinstance(c, dict) and c.get("role") == role), {})
            temp = contact.get("relationship_temperature", "未知")
            if temp not in ("热", "温", "冷", "未知"):
                temp = "未知"
            nodes.append(DecisionNode(
                node_id=f"node-{i + 1}", role=role, name=contact.get("name"), decision_role=mapped,
                heat="未知", relation_temperature=temp,
                relationship_note=str(contact.get("note", "该角色尚无直接关系记录；客户整体关系不等于个人关系")),
                is_inferred=not bool(contact), source_evidence=[Evidence(source="crm_history.contacts", quote=json.dumps(contact, ensure_ascii=False))] if contact else [],
            ))
        return NetworkLayer(decision_heatmap=nodes, account_relationship_temperature=account_temp, relationship_summary=summary)

    def _build_pain(self, signals: SignalLayer, template: IndustryTemplate) -> PainPointLayer:
        # 关键词匹配只提供关联依据；没有信号匹配时仍列行业访谈假设，但明确无证据支持。
        candidates: list[tuple[int, int, PainHypothesis]] = []
        for i, item in enumerate(template.pain_library):
            matches = [s for s in signals.signals if s.timeliness == "有效" and s.polarity == "正面" and any(k.casefold() in (s.title + s.category + " ".join(e.quote for e in s.source_evidence)).casefold() for k in item.signal_keywords)]
            related = [signal.signal_id for signal in matches]
            question = item.validation_question
            if matches:
                question = f"围绕‘{matches[0].title}’这一采集线索，请核实：{question}"
            candidates.append((len(related), i, PainHypothesis(
                hypothesis_id=f"pain-{i + 1}", template_id=item.template_id, description=item.description,
                validation_question=question, related_signal_ids=related,
                reasoning="行业模板与采集信号关键词关联，仍需验证" if related else "仅来自行业模板，暂无匹配客户信号，不得当成事实",
            )))
        selected = sorted(candidates, key=lambda x: (-x[0], x[1]))[:3]
        # 输出保持模板顺序，方便销售稳定地引用“假设2”。
        return PainPointLayer(hypotheses=[p for _, _, p in sorted(selected, key=lambda x: x[1])])

    def _build_hook(self, base: BaseLayer, signals: SignalLayer, network: NetworkLayer, pain: PainPointLayer, template: IndustryTemplate) -> HookLayer:
        rank = {"高": 2, "中": 1, "低": 0}
        anchor = max((s for s in signals.signals if s.timeliness == "有效" and s.polarity == "正面"),
                     key=lambda s: rank.get(s.urgency, -1), default=None)
        roles = [n for n in network.decision_heatmap if n.decision_role in ("关键影响者", "关键使用者", "合规影响者")]
        opening = (f"公开采集记录提到‘{anchor.title}’（时间与现状仍需核实）。这一动作目前对哪项跨部门流程影响最大？" if anchor else "目前最值得优先改善的跨部门流程是哪一项？希望先了解你们的实际优先级。")
        stage_text = "公司阶段待核实" if base.stage == "unknown" else f"以‘{base.stage}’作为待验证阶段假设"
        return HookLayer(
            opening_angle=opening,
            viewpoint_framework=f"{stage_text}；{template.hook_strategies[0]}。建议先向{'、'.join(n.role for n in roles) or '实际负责人员'}核实。",
            suggested_visit_objectives=["核实阶段判断及近期动作的实际影响", "逐项验证最终痛点假设层的问题，以人工修正后的内容为准", "确认实际决策链、参与角色与一个可验证的后续步骤"],
            related_signal_ids=[anchor.signal_id] if anchor else [], recommended_node_ids=[n.node_id for n in roles],
            source_evidence=anchor.source_evidence if anchor else [],
        )

    def apply_patch(self, card: AccountIntelCard, patch: PatchRecord) -> AccountIntelCard:
        """JSON Pointer不可变修正；拒绝过期原值和破坏整卡约束的操作。"""
        if any(p.patch_id == patch.patch_id for p in card.patches_applied):
            raise ValueError("修正ID已应用")
        for key in ("pain", "hook"):
            if card.derived_dependencies.get(key) != card.dependency_snapshot(key):
                raise StaleCardError(f"{key} 与当前卡片不一致，待重算")
        document = card.model_dump(mode="json")
        layer = document["layers"][patch.target_layer]
        if patch.field_path == "":
            if patch.operation != "replace" or layer != patch.original_value:
                raise ValueError("整层只能替换，且原值必须匹配")
            document["layers"][patch.target_layer] = copy.deepcopy(patch.corrected_value)
        else:
            if not patch.field_path.startswith("/") or re.search(r"~(?![01])", patch.field_path):
                raise ValueError("field_path必须是有效JSON Pointer")
            tokens = [t.replace("~1", "/").replace("~0", "~") for t in patch.field_path[1:].split("/")]
            parent: Any = layer
            try:
                for token in tokens[:-1]:
                    parent = parent[self._index(token, len(parent), False)] if isinstance(parent, list) else parent[token]
                token = tokens[-1]
                if isinstance(parent, list):
                    index = self._index(token, len(parent), patch.operation == "add")
                    if patch.operation == "add":
                        if patch.original_value is not None:
                            raise ValueError("插入操作原值必须为null")
                        parent.insert(index, copy.deepcopy(patch.corrected_value))
                    else:
                        self._check_original(parent[index], patch)
                        if patch.operation == "remove":
                            parent.pop(index)
                        else:
                            parent[index] = copy.deepcopy(patch.corrected_value)
                elif isinstance(parent, dict):
                    if patch.operation == "add":
                        if token in parent or patch.original_value is not None:
                            raise ValueError("新增字段必须不存在，原值必须为null")
                    else:
                        self._check_original(parent[token], patch)
                    if patch.operation == "remove":
                        del parent[token]
                    else:
                        parent[token] = copy.deepcopy(patch.corrected_value)
                else:
                    raise ValueError("路径父节点不是对象或数组")
            except (KeyError, IndexError, TypeError) as exc:
                raise ValueError("修正路径不存在或类型错误") from exc
        if not re.fullmatch(r"\d+\.\d+\.\d+", card.version):
            raise ValueError("版本必须使用major.minor.patch格式")
        major, minor, revision = card.version.split(".")
        document["version"] = f"{major}.{minor}.{int(revision) + 1}"
        document["patches_applied"].append(patch.model_dump(mode="json"))
        # 逐层校验后刷新派生内容，最后再检查跨层引用。
        layers = TypeAdapter(dict[LayerKey, LayerModel]).validate_python(document["layers"])
        base = layers["base"]
        signals = layers["signal"]
        network = layers["network"]
        pain = layers["pain"]
        assert isinstance(base, BaseLayer) and isinstance(signals, SignalLayer)
        assert isinstance(network, NetworkLayer) and isinstance(pain, PainPointLayer)
        if patch.target_layer == "base" and base.stage != card.layers["base"].stage:
            base.stage_source = "manual"
            base.stage_reason = "销售人工修正，待核实"
            base.source_evidence = []
        if patch.target_layer == "network":
            previous_nodes = {node.node_id: node for node in card.layers["network"].decision_heatmap}
            for node in network.decision_heatmap:
                previous = previous_nodes.get(node.node_id)
                if previous and (previous.role, previous.name) != (node.role, node.name):
                    node.source_evidence = [Evidence(source="销售人工修正", quote=node.role, source_type="manual")]
        if patch.target_layer != "hook":
            industry = base.industry_tags[0] if base.industry_tags else ""
            template = self._resolve_template(industry, card.template_key)
            if patch.target_layer == "signal":
                old_signals = {s.signal_id: s for s in card.layers["signal"].signals}
                for signal in signals.signals:
                    previous = old_signals.get(signal.signal_id)
                    if previous != signal:
                        # 手动修改的信号不能沿用原记录的证据等级。
                        if previous and previous.title != signal.title and previous.source_evidence == signal.source_evidence:
                            signal.source_evidence = [Evidence(source="销售人工修正", quote=signal.title, source_type="manual")]
                        else:
                            for evidence in signal.source_evidence:
                                evidence.source_type = "manual"
                        signal.timeliness = "unknown"
                        signal.urgency = "unknown"
                        signal.urgency_reason = "人工修正，来源及业务影响待核实"
                if base.stage_source != "manual":
                    base.stage, base.stage_reason, base.source_evidence = self._stage_from_signals(signals)
                pain = self._build_pain(signals, template)
                layers["pain"] = pain
            layers["hook"] = self._build_hook(base, signals, network, pain, template)
        document["layers"] = layers
        return self._stamp_dependencies(AccountIntelCard.model_validate(document))

    @staticmethod
    def _index(token: str, length: int, adding: bool) -> int:
        if adding and token == "-":
            return length
        if not re.fullmatch(r"0|[1-9]\d*", token):
            raise ValueError("数组索引无效")
        index = int(token)
        if index > length or (index == length and not adding):
            raise ValueError("数组索引越界")
        return index

    @staticmethod
    def _check_original(value: Any, patch: PatchRecord) -> None:
        if value != patch.original_value:
            raise ValueError("原值不匹配，卡片可能已被修改")
        if patch.operation == "remove" and patch.corrected_value is not None:
            raise ValueError("删除操作修正值必须为null")

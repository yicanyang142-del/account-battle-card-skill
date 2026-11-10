"""所有输入、模板、档案及修正模型。事实与推断分别标记。"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from typing import Annotated, Any, Literal, Union
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, JsonValue, PositiveInt, field_validator, model_validator

LayerKey = Literal["base", "signal", "network", "pain", "hook"]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class Evidence(Model):
    source: str = Field(description="采集记录来源；未提供时明确标记")
    quote: str = Field(description="输入记录摘录，不代表独立核实")
    url: str | None = None
    occurred_at: datetime | None = None
    source_type: Literal["fact", "inference", "manual"] = "inference"
    published_at: datetime | None = None


class Event(Model):
    title: str = Field(min_length=1)
    description: str = ""
    source: str = "原始采集记录（来源未提供）"
    url: str | None = None
    occurred_at: datetime | None = None
    published_at: datetime | None = None
    deadline_at: datetime | None = None
    claim_key: str | None = None
    polarity: Literal["正面", "否定", "失败", "unknown"] = "unknown"

    @field_validator("occurred_at", "published_at", "deadline_at")
    @classmethod
    def aware_time(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("事件时间必须带时区")
        return value


class NewsEvent(Event):
    category: str = "news"
    source_excerpt: str | None = None


class JobPost(Model):
    title: str = Field(min_length=1)
    description: str = ""
    department: str = ""
    source: str = "招聘采集记录（来源未提供）"
    url: str | None = None
    posted_at: datetime | None = None

    @field_validator("posted_at")
    @classmethod
    def aware_time(cls, value: datetime | None) -> datetime | None:
        return Event.aware_time(value)


class RawCollectedData(Model):
    company_name: str = Field(min_length=1)
    industry: str = ""
    base_info_dict: dict[str, Any] = Field(default_factory=dict)
    news_events: list[NewsEvent] = Field(default_factory=list)
    funding_events: list[Event] = Field(default_factory=list)
    job_postings: list[JobPost] = Field(default_factory=list)
    leadership_changes: list[Event] = Field(default_factory=list)
    tech_indicators: list[str] = Field(default_factory=list)
    crm_history: dict[str, Any] | None = None


RawData = RawCollectedData


class PainHypothesisTemplate(Model):
    template_id: str
    description: str
    validation_question: str
    signal_keywords: list[str] = Field(min_length=1)


class IndustryTemplate(Model):
    industry_key: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    signal_patterns: list[str] = Field(min_length=1)
    pain_library: list[PainHypothesisTemplate] = Field(min_length=3)
    role_map: dict[str, str]
    hook_strategies: list[str] = Field(min_length=1)
    evidence_window_days: dict[str, PositiveInt] = Field(default_factory=lambda: {"融资": 365, "新闻": 180})
    urgent_deadline_days: PositiveInt = 30

    @model_validator(mode="after")
    def unique_pains(self) -> IndustryTemplate:
        if len({p.template_id for p in self.pain_library}) != len(self.pain_library):
            raise ValueError("痛点模板ID必须唯一")
        return self


class BaseLayer(Model):
    kind: Literal["base"] = "base"
    stage: Literal["扩张", "优化", "转型", "防御", "unknown"]
    stage_source: Literal["fact", "inference", "manual"] = "inference"
    stage_reason: str = Field(description="阶段推断依据与不确定性")
    business_model_summary: str
    scale: str
    industry_tags: list[str]
    source_evidence: list[Evidence]


class Signal(Model):
    signal_id: str
    category: str
    title: str
    inference: str = Field(description="待验证的销售判断，不是客户事实")
    urgency: Literal["高", "中", "低", "unknown"]
    urgency_reason: str
    source_evidence: list[Evidence] = Field(min_length=1)
    polarity: Literal["正面", "否定", "失败", "unknown"] = "unknown"
    timeliness: Literal["有效", "过期", "冲突", "unknown"] = "unknown"
    claim_key: str | None = None


class SignalLayer(Model):
    kind: Literal["signal"] = "signal"
    signals: list[Signal]


class DecisionNode(Model):
    node_id: str
    role: str
    name: str | None = None
    decision_role: str
    heat: Literal["高", "中", "低", "未知"] = "未知"
    relation_temperature: Literal["热", "温", "冷", "未知"] = "未知"
    relationship_note: str = "无直接关系记录"
    is_inferred: bool = True
    resistance_hypothesis: str = "待确认：预算、交付风险和现有流程可能形成阻力"
    source_evidence: list[Evidence] = Field(default_factory=list)


class NetworkLayer(Model):
    kind: Literal["network"] = "network"
    decision_heatmap: list[DecisionNode]
    account_relationship_temperature: Literal["热", "温", "冷", "未知"]
    relationship_summary: str


class PainHypothesis(Model):
    hypothesis_id: str
    template_id: str
    description: str
    validation_question: str
    related_signal_ids: list[str]
    status: Literal["待验证", "人工修正", "已验证", "已排除"] = "待验证"
    reasoning: str


class PainPointLayer(Model):
    kind: Literal["pain"] = "pain"
    hypotheses: list[PainHypothesis] = Field(max_length=3)


class HookLayer(Model):
    kind: Literal["hook"] = "hook"
    opening_angle: str
    viewpoint_framework: str
    suggested_visit_objectives: list[str]
    related_signal_ids: list[str]
    recommended_node_ids: list[str]
    source_evidence: list[Evidence]


LayerModel = Annotated[Union[BaseLayer, SignalLayer, NetworkLayer, PainPointLayer, HookLayer], Field(discriminator="kind")]


class PatchRecord(Model):
    patch_id: str = Field(default_factory=lambda: str(uuid4()))
    target_layer: LayerKey
    field_path: str = Field(description="相对层的JSON Pointer，如 /hypotheses/1/description；空串代表整层")
    original_value: JsonValue = Field(...)
    corrected_value: JsonValue = Field(..., description="删除操作必须为null；插入操作的原值必须为null")
    reason: str = Field(min_length=1)
    patched_at: datetime = Field(default_factory=utc_now)
    operation: Literal["replace", "remove", "add"] = "replace"

    @field_validator("patched_at")
    @classmethod
    def aware_patch_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("修正时间必须带时区")
        return value

    @field_validator("reason")
    @classmethod
    def meaningful_reason(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("修正原因不能为空白")
        return value


class AccountIntelCard(Model):
    card_id: str = Field(default_factory=lambda: str(uuid4()))
    company_name: str
    generated_at: datetime = Field(default_factory=utc_now)
    layers: dict[LayerKey, LayerModel]
    patches_applied: list[PatchRecord] = Field(default_factory=list)
    version: str = "1.0.0"
    template_key: str | None = None
    derived_dependencies: dict[LayerKey, dict[LayerKey, str]] = Field(default_factory=dict)

    def dependency_snapshot(self, layer: LayerKey) -> dict[LayerKey, str]:
        dependencies: dict[LayerKey, tuple[LayerKey, ...]] = {
            "pain": ("signal", "pain"),
            "hook": ("base", "signal", "network", "pain", "hook"),
        }
        return {
            key: sha256(json.dumps(self.layers[key].model_dump(mode="json"), sort_keys=True,
                                   ensure_ascii=False).encode("utf-8")).hexdigest()
            for key in dependencies.get(layer, ())
        }


    @model_validator(mode="after")
    def consistent_layers(self) -> AccountIntelCard:
        if set(self.layers) != {"base", "signal", "network", "pain", "hook"}:
            raise ValueError("卡片必须且只能包含五层")
        if any(k != v.kind for k, v in self.layers.items()):
            raise ValueError("层键与层类型不匹配")
        signal = self.layers["signal"]
        network = self.layers["network"]
        pain = self.layers["pain"]
        hook = self.layers["hook"]
        assert isinstance(signal, SignalLayer) and isinstance(network, NetworkLayer)
        assert isinstance(pain, PainPointLayer) and isinstance(hook, HookLayer)
        for ids in ([s.signal_id for s in signal.signals], [n.node_id for n in network.decision_heatmap], [p.hypothesis_id for p in pain.hypotheses]):
            if len(ids) != len(set(ids)):
                raise ValueError("层内标识必须唯一")
        signal_ids = {s.signal_id for s in signal.signals}
        if not set(hook.related_signal_ids).issubset(signal_ids) or any(not set(p.related_signal_ids).issubset(signal_ids) for p in pain.hypotheses):
            raise ValueError("存在悬空的信号引用，请同步修正关联层")
        if not set(hook.recommended_node_ids).issubset({n.node_id for n in network.decision_heatmap}):
            raise ValueError("存在悬空的决策角色引用")
        return self


class StaleCardError(ValueError):
    """当前卡片缺少有效的派生内容，请重新生成。"""

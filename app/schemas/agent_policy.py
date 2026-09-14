"""
Schemas para políticas configuráveis de automação, contexto e revisão humana.
"""
from enum import Enum
from typing import List, Optional

from pydantic import Field

from app.schemas.base import CamelCaseSchema, WriteSchema


class AutomationStageMode(str, Enum):
    human_only = "human_only"
    ai_only = "ai_only"
    human_in_the_loop = "human_in_the_loop"
    ai_with_human_approval = "ai_with_human_approval"


class CostProfile(str, Enum):
    economic = "economic"
    balanced = "balanced"
    quality = "quality"


class ContextMode(str, Enum):
    essential = "essential"
    balanced = "balanced"
    full = "full"


class AutomationPreset(str, Enum):
    economic = "economic"
    balanced = "balanced"
    supervised = "supervised"
    custom = "custom"


class AutomationPresetRead(CamelCaseSchema):
    key: AutomationPreset
    label: str
    description: str


class StagePolicyRead(CamelCaseSchema):
    stage_key: str
    label: str
    description: str
    mode: AutomationStageMode
    agent_key: str
    model: Optional[str] = None
    cost_profile: CostProfile = CostProfile.balanced
    context_mode: ContextMode = ContextMode.balanced
    use_rag: bool = True
    multimodal_enabled: bool = True
    human_review_required: bool = False


class StagePolicyUpdate(WriteSchema):
    stage_key: str
    mode: AutomationStageMode
    agent_key: str
    model: Optional[str] = None
    cost_profile: CostProfile = CostProfile.balanced
    context_mode: ContextMode = ContextMode.balanced
    use_rag: bool = True
    multimodal_enabled: bool = True
    human_review_required: bool = False


class WorkspaceAutomationSettingsRead(CamelCaseSchema):
    onboarding_mode: str
    preset: AutomationPreset = AutomationPreset.balanced
    available_presets: List[AutomationPresetRead] = Field(default_factory=list)
    stage_policies: List[StagePolicyRead] = Field(default_factory=list)


class WorkspaceAutomationSettingsUpdate(WriteSchema):
    onboarding_mode: Optional[str] = None
    preset: Optional[AutomationPreset] = None
    stage_policies: List[StagePolicyUpdate] = Field(default_factory=list)

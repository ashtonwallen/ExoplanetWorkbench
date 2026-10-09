from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Search(StrictModel):
    target: str = Field(min_length=1, max_length=150)
    mission: Literal["TESS", "Kepler", "K2"] = "TESS"


class Analysis(Search):
    sectors: list[int] = Field(default_factory=list, max_length=30)
    max_products: int = Field(default=2, ge=1, le=30)
    min_period: float = Field(default=0.5, ge=0.2, le=100)
    max_period: float = Field(default=10, ge=0.3, le=200)
    min_duration: float = Field(default=0.5, ge=0.2, le=12, description="Hours")
    max_duration: float = Field(default=5, ge=0.3, le=24, description="Hours")
    detrend: Literal["median", "savgol", "none"] = "median"
    window_days: float = Field(default=1.5, ge=0.2, le=20)
    quality: Literal["default", "strict"] = "default"
    max_signals: int = Field(default=2, ge=1, le=5)
    max_download_mb: int = Field(default=300, ge=5, le=3000)
    timeout_seconds: int = Field(default=1200, ge=60, le=14400)

    @model_validator(mode="after")
    def ranges(self):
        if self.max_period <= self.min_period:
            raise ValueError("Maximum period must exceed minimum period")
        if self.max_duration <= self.min_duration or self.max_duration / 24 >= self.min_period:
            raise ValueError("Durations must increase and stay below the minimum period")
        if self.detrend != "none" and self.window_days < 5 * self.max_duration / 24:
            raise ValueError("Detrending window must be at least five times the longest searched transit")
        return self


class Batch(StrictModel):
    targets: list[str] = Field(min_length=1, max_length=1000)
    config: Analysis


class CandidateRef(StrictModel):
    investigation_id: str = Field(pattern=r"^[a-f0-9]{16}$")
    signal_index: int = Field(default=0, ge=0, le=4)


class Injection(CandidateRef):
    trials: int = Field(default=6, ge=2, le=40)
    depth_ppm: float = Field(default=1000, ge=50, le=50000)
    period: float = Field(default=3.18, ge=0.2, le=100)
    seed: int = Field(default=42, ge=0, le=2147483647)


class Alias(CandidateRef):
    period: float = Field(gt=0.1, le=200)


class AIConfig(StrictModel):
    provider: Literal["openai", "anthropic", "gemini", "openrouter", "local"] = "openai"
    model: str = Field(default="", max_length=150)
    base_url: str = Field(default="http://127.0.0.1:1234/v1", max_length=300)
    api_key: str | None = Field(default=None, max_length=600)
    clear_key: bool = False
    temperature: float | None = Field(default=None, ge=0, le=2)
    reasoning: Literal["", "low", "medium", "high"] = ""
    max_iterations: int = Field(default=6, ge=1, le=30)
    max_analyses: int = Field(default=3, ge=0, le=30)
    max_output_tokens: int = Field(default=2048, ge=256, le=16000)
    budget_usd: float = Field(default=1, ge=0, le=100)
    input_usd_per_million: float = Field(default=0, ge=0, le=1000)
    output_usd_per_million: float = Field(default=0, ge=0, le=10000)


class Chat(StrictModel):
    conversation_id: str = Field(pattern=r"^[a-f0-9]{16}$")
    text: str = Field(min_length=1, max_length=12000)
    investigation_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{16}$")


class ResearchGoal(StrictModel):
    goal: str = Field(min_length=8, max_length=12000)
    max_targets: int = Field(default=3, ge=1, le=30)
    max_operations: int = Field(default=10, ge=1, le=80)
    max_model_calls: int = Field(default=20, ge=3, le=80)
    max_input_mb: int = Field(default=600, ge=10, le=5000)
    timeout_seconds: int = Field(default=3600, ge=120, le=14400)
    budget_usd: float = Field(default=1, ge=0, le=100)


class TargetDiscovery(StrictModel):
    region: Literal['compare_poles', 'north_pole', 'south_pole', 'custom'] = 'compare_poles'
    ra: float | None = Field(default=None, ge=0, lt=360)
    dec: float | None = Field(default=None, ge=-90, le=90)
    radius_degrees: float = Field(default=1, ge=.1, le=3)
    max_tmag: float = Field(default=12, ge=7, le=15)
    max_radius_solar: float = Field(default=1.5, ge=.2, le=3)
    min_sectors: int = Field(default=2, ge=1, le=100)
    rank_by: Literal['coverage', 'small_stars', 'brightness'] = 'coverage'
    limit: int = Field(default=12, ge=1, le=30)

    @model_validator(mode='after')
    def coordinates(self):
        if self.region == 'custom' and (self.ra is None or self.dec is None):
            raise ValueError('Custom regions require ICRS RA and declination in degrees')
        return self


class ResearchPlan(StrictModel):
    strategy: str = Field(min_length=20, max_length=5000)
    steps: list[str] = Field(min_length=2, max_length=12)
    selection_reason: str = Field(min_length=10, max_length=2000)


class ResearchConclusion(StrictModel):
    assessment: str = Field(min_length=30, max_length=18000)
    investigation_ids: list[str] = Field(default_factory=list, max_length=40)
    limitations: list[str] = Field(min_length=1, max_length=20)
    next_steps: list[str] = Field(default_factory=list, max_length=12)

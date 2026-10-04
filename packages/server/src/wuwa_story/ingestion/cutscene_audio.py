"""Independent, source-verified cutscene stems on the original movie timeline."""

from typing import Literal

from pydantic import Field, model_validator

from wuwa_story.ingestion.cutscenes import StrictModel


class AudioStem(StrictModel):
    role: Literal["voice", "music", "effects", "mixed"]
    language: Literal["en", "ja", "ko", "zh"] | None = None
    path: str = Field(min_length=1)
    sources: list[str] = Field(min_length=1, max_length=100)
    evidence: str = Field(min_length=1)

    @model_validator(mode="after")
    def language_matches_role(self):
        if (self.role == "voice") != (self.language is not None):
            raise ValueError("Voice stems require a language; other stems are language independent")
        return self


class CutsceneAudioRecipe(StrictModel):
    asset: str = Field(pattern=r"^asset:ue:/Game/")
    asset_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    duration: float = Field(gt=0)
    stems: list[AudioStem] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def unique_roles(self):
        keys = [(stem.role, stem.language) for stem in self.stems]
        if len(keys) != len(set(keys)):
            raise ValueError("Mix each role/language into one stem before publication")
        if any(stem.role == "mixed" for stem in self.stems) and len(self.stems) != 1:
            raise ValueError("A mixed soundtrack cannot advertise independent stems")
        return self

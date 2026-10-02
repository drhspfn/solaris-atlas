"""Validated playback graphs shared by cutscene publishing and the API."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Clip(StrictModel):
    id: str = Field(min_length=1)
    kind: Literal["clip"] = "clip"
    asset: str = Field(pattern=r"^asset:ue:/Game/")
    segment: str | None = Field(default=None, pattern=r"^segment-[a-z0-9-]+$")
    start: float = Field(default=0, ge=0)
    end: float | None = Field(default=None, gt=0)
    next: str | None = None

    @model_validator(mode="after")
    def valid_range(self):
        if self.end is not None and self.end <= self.start:
            raise ValueError("Clip end must follow its start")
        return self


class Option(StrictModel):
    label: str = Field(min_length=1)
    next: str = Field(min_length=1)


class Choice(StrictModel):
    id: str = Field(min_length=1)
    kind: Literal["choice"] = "choice"
    prompt: str = Field(min_length=1)
    options: list[Option] = Field(min_length=2, max_length=20)


class PlaybackFlow(StrictModel):
    version: Literal[1] = 1
    entry: str
    nodes: list[Annotated[Clip | Choice, Field(discriminator="kind")]] = Field(
        min_length=1, max_length=200
    )
    evidence: str = Field(min_length=1)

    @model_validator(mode="after")
    def valid_graph(self):
        nodes = {node.id: node for node in self.nodes}
        if len(nodes) != len(self.nodes) or self.entry not in nodes:
            raise ValueError("Duplicate nodes or missing playback entry")
        visiting, visited = set(), set()

        def visit(key):
            if key not in nodes:
                raise ValueError("Playback target is missing")
            if key in visiting:
                raise ValueError("Playback cycles are not supported")
            if key in visited:
                return
            visiting.add(key)
            node = nodes[key]
            targets = (
                [option.next for option in node.options]
                if isinstance(node, Choice)
                else ([node.next] if node.next else [])
            )
            for target in targets:
                visit(target)
            visiting.remove(key)
            visited.add(key)

        visit(self.entry)
        if visited != set(nodes):
            raise ValueError("Unreachable playback nodes")
        return self


class Soundtrack(StrictModel):
    bank: str
    start_seconds: float = Field(default=0, ge=0)
    end_seconds: float | None = Field(default=None, gt=0)
    gain_db: float = Field(default=0, ge=-96, le=12)

    @model_validator(mode="after")
    def valid_range(self):
        if self.end_seconds is not None and self.end_seconds <= self.start_seconds:
            raise ValueError("Soundtrack end must follow its start")
        return self


class VideoInput(StrictModel):
    asset: str = Field(pattern=r"^asset:ue:/Game/")
    soundtrack: list[Soundtrack] = Field(default_factory=list, max_length=32)


class CutsceneRecipe(StrictModel):
    cutscene: str = Field(pattern=r"^cutscene:.+")
    asset_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    compare_variants: bool = False
    videos: list[VideoInput] = Field(min_length=1, max_length=100)
    flow: PlaybackFlow

    @model_validator(mode="after")
    def valid_assets(self):
        assets = {video.asset for video in self.videos}
        if len(assets) != len(self.videos):
            raise ValueError("Duplicate video assets")
        if any(isinstance(node, Clip) and node.asset not in assets for node in self.flow.nodes):
            raise ValueError("Playback clip references an unpublished asset")
        return self

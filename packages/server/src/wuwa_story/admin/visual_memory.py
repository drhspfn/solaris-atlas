"""Visual Memory console page."""

from starlette_admin import CustomView
from wuwa_story.visual_memory.kinds import KIND_LABELS, VisualKind


class VisualMemory(CustomView):
    def __init__(self) -> None:
        super().__init__("Visual Memory", path="/visual-memory", name="visual-memory",
                         template_path="visual_memory.html")

    async def render(self, request, templates):
        return templates.TemplateResponse(request=request, name=self.template_path,
            context={"title": self.title(request), "visual_kinds": [
                {"value": kind.value, "label": KIND_LABELS[kind]} for kind in VisualKind
            ]})

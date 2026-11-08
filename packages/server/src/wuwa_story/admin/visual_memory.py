"""Visual Memory console page."""

from starlette_admin import CustomView


class VisualMemory(CustomView):
    def __init__(self) -> None:
        super().__init__("Visual Memory", path="/visual-memory", name="visual-memory",
                         template_path="visual_memory.html")

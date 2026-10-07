"""Resolve external extraction tools (FModelCLI, CUE4Parse CLI, vgmstream) with actionable errors."""

from __future__ import annotations

import os
from pathlib import Path

DEFAULT_TOOLS_DIR = "/app/tools"

# env var -> (default relative path inside WUWA_TOOLS_DIR, human description)
TOOLS = {
    "WUWA_FMODEL_PATH": ("fmodelcli/FModelCLI", "FModelCLI extractor"),
    "WUWA_TEXTURE_CONVERTER_PATH": ("cue-cli/cue4parse", "CUE4Parse CLI texture/audio converter"),
    "WUWA_VGMSTREAM_PATH": ("vgmstream-cli", "vgmstream-cli audio decoder"),
}


def tool_path(env_name: str) -> Path:
    """Return the configured tool path, falling back to ``$WUWA_TOOLS_DIR/<default>``.

    Raises a RuntimeError naming the missing file and how to provide it, instead of a bare KeyError.
    """
    default, description = TOOLS[env_name]
    configured = os.getenv(env_name)
    path = Path(configured) if configured else Path(os.getenv("WUWA_TOOLS_DIR") or DEFAULT_TOOLS_DIR) / default
    if not path.is_file():
        raise RuntimeError(
            f"{description} not found at {path}. Rebuild the worker image "
            f"or set {env_name} to a verified tool's absolute path."
        )
    return path


def asset_workspace() -> Path:
    """Client download workspace shared by the API (read-only) and the worker."""
    return Path(os.getenv("WUWA_ASSET_WORKSPACE", "/var/lib/wuwa-worker/client-assets")).resolve()

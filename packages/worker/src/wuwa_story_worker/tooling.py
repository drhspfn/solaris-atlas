"""Resolve external extraction tools (FModelCLI, CUE4Parse CLI, vgmstream) with actionable errors."""

from __future__ import annotations

import os
from pathlib import Path

DEFAULT_TOOLS_DIR = "/opt/wuwa-tools"

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
    path = Path(configured) if configured else Path(os.getenv("WUWA_TOOLS_DIR", DEFAULT_TOOLS_DIR)) / default
    if not path.is_file():
        raise RuntimeError(
            f"{description} not found at {path}. Install it on the server "
            f"(scripts/install-wuwa-tools.sh) or set {env_name} to its absolute path."
        )
    _ensure_native_libs(Path(os.getenv("WUWA_TOOLS_DIR", DEFAULT_TOOLS_DIR)))
    return path

def _ensure_native_libs(tools_dir: Path) -> None:
    if not (tools_dir / "libdetex.so").is_file():
        return
    for target_dir in [tools_dir / "fmodelcli/.data", Path("/tmp")]:
        target_dir.mkdir(parents=True, exist_ok=True)
        for src, dst in [
            ("libdetex.so", "Detex.dll"),
            ("libz-ng.so", "zlib-ng2.dll"),
            ("liboo2corelinux64.so.9", "liboodle-data-shared.so"),
        ]:
            src_path = tools_dir / src
            dst_path = target_dir / dst
            if src_path.is_file():
                if dst_path.exists() or dst_path.is_symlink():
                    try:
                        dst_path.unlink()
                    except Exception:
                        pass
                try:
                    dst_path.symlink_to(src_path)
                except Exception:
                    pass



def asset_workspace() -> Path:
    """Client download workspace shared by the API (read-only) and the worker."""
    return Path(os.getenv("WUWA_ASSET_WORKSPACE", "/var/lib/wuwa-worker/client-assets")).resolve()

# Required for FModelCLI and CUE4Parse.CLI (.NET self-contained) in slim Linux images lacking libicu
os.environ["DOTNET_SYSTEM_GLOBALIZATION_INVARIANT"] = "1"
# Ensure .NET can find native libraries (like oo2core) next to the tools
tools_dir = os.getenv("WUWA_TOOLS_DIR", DEFAULT_TOOLS_DIR)
os.environ["LD_LIBRARY_PATH"] = f"{tools_dir}:{os.environ.get('LD_LIBRARY_PATH', '')}"

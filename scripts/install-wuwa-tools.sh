#!/usr/bin/env bash
# Install the Linux extraction tools used by the production asset worker into WUWA_TOOLS_HOST_PATH
# (default /opt/wuwa-tools, mounted read-only at /opt/wuwa-tools in snapshot-worker).
#
#   FModelCLI            -> $TOOLS/FModelCLI           (WUWA_FMODEL_PATH)
#   CUE4Parse.CLI        -> $TOOLS/cue-cli/cue4parse   (WUWA_TEXTURE_CONVERTER_PATH)
#   vgmstream-cli        -> $TOOLS/vgmstream-cli       (WUWA_VGMSTREAM_PATH)
#
# FModelCLI and CUE4Parse.CLI only publish Windows binaries, so they are built from source for
# linux-x64 inside the official .NET SDK image. Pinned refs are overridable via environment.
set -euo pipefail

TOOLS="${WUWA_TOOLS_HOST_PATH:-/opt/wuwa-tools}"
FMODELCLI_REF="${FMODELCLI_REF:-ad969ec899a235b4b5b7706df9244112233dd7de}"
CUE4PARSE_CLI_REF="${CUE4PARSE_CLI_REF:-cli-0.2.0}"
DOTNET_IMAGE="${DOTNET_IMAGE:-mcr.microsoft.com/dotnet/sdk:10.0}"

mkdir -p "$TOOLS/cue-cli"

build_dotnet() { # repo ref project-glob out-dir
  local repo="$1" ref="$2" glob="$3" out="$4"
  docker run --rm -v "$TOOLS:/out" -e REPO="$repo" -e REF="$ref" -e GLOB="$glob" -e OUT="$out" "$DOTNET_IMAGE" bash -ec '
    apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq git cmake clang build-essential >/dev/null
    git clone --recursive "$REPO" /src
    cd /src && git checkout --recurse-submodules "$REF"
    project=$(find . -path ./upstream -prune -o -name "$GLOB" -print | head -n1)
    test -n "$project" || { echo "No project matching $GLOB"; exit 1; }
    dotnet publish "$project" -c Release -r linux-x64 --self-contained true \
      -p:PublishSingleFile=true -p:PublishReadyToRun=false -o "/out/$OUT"
  '
}

build_dotnet https://github.com/Herselfta/FModelCLI.git "$FMODELCLI_REF" 'FModelCLI.csproj' _fmodelcli
install -m 755 "$TOOLS/_fmodelcli/FModelCLI" "$TOOLS/FModelCLI"

build_dotnet https://github.com/joric/CUE4Parse.CLI.git "$CUE4PARSE_CLI_REF" '*CLI*.csproj' _cue4parse
cli_bin=$(find "$TOOLS/_cue4parse" -maxdepth 1 -type f -perm -u+x ! -name '*.so' ! -name '*.dll' | head -n1)
test -n "$cli_bin" || { echo "CUE4Parse CLI build produced no executable" >&2; exit 1; }
install -m 755 "$cli_bin" "$TOOLS/cue-cli/cue4parse"

tmp=$(mktemp -d)
curl -fsSL https://github.com/vgmstream/vgmstream/releases/latest/download/vgmstream-linux-cli.tar.gz | tar -xz -C "$tmp"
install -m 755 "$(find "$tmp" -type f -name vgmstream-cli | head -n1)" "$TOOLS/vgmstream-cli"
rm -rf "$tmp"

echo "Installed tools in $TOOLS:"
ls -l "$TOOLS" "$TOOLS/cue-cli"

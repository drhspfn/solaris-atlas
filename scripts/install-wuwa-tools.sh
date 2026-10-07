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
DOTNET_IMAGE="${DOTNET_IMAGE:-mcr.microsoft.com/dotnet/sdk:9.0}"

mkdir -p "$TOOLS/cue-cli"

build_dotnet() { # repo ref project-glob out-dir
  local repo="$1" ref="$2" glob="$3" out="$4"
  docker run --rm -v "$TOOLS:/out" -e REPO="$repo" -e REF="$ref" -e GLOB="$glob" -e OUT="$out" "$DOTNET_IMAGE" bash -ec '
    apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq git cmake clang build-essential >/dev/null
    git clone --recursive "$REPO" /src
    cd /src && git checkout --recurse-submodules "$REF"
    sed -i "s/net10.0/net9.0/g" Directory.Build.props || true
    project=$(find . -path ./upstream -prune -o -name "$GLOB" -print | head -n1)
    test -n "$project" || { echo "No project matching $GLOB"; exit 1; }
    dotnet publish "$project" -c Release -r linux-x64 --self-contained true \
      -p:PublishReadyToRun=false -o "/out/$OUT"
  '
}

build_dotnet https://github.com/Herselfta/FModelCLI.git "$FMODELCLI_REF" 'FModelCLI.csproj' fmodelcli
chmod +x "$TOOLS/fmodelcli/FModelCLI"

build_dotnet https://github.com/joric/CUE4Parse.CLI.git "$CUE4PARSE_CLI_REF" '*CLI*.csproj' cue-cli
if [ -f "$TOOLS/cue-cli/CUE4Parse.CLI" ]; then mv "$TOOLS/cue-cli/CUE4Parse.CLI" "$TOOLS/cue-cli/cue4parse"; fi
chmod +x "$TOOLS/cue-cli/cue4parse"

tmp=$(mktemp -d)
curl -fsSL https://github.com/vgmstream/vgmstream/releases/latest/download/vgmstream-linux.zip -o "$tmp/vgmstream.zip"
python3 -c "import zipfile; zipfile.ZipFile('$tmp/vgmstream.zip').extractall('$tmp')"
install -m 755 "$(find "$tmp" -type f -name vgmstream-cli | head -n1)" "$TOOLS/vgmstream-cli"
rm -rf "$tmp"

# Build/Download native libraries for CUE4Parse on Linux
docker run --rm -v "$TOOLS:/out" "$DOTNET_IMAGE" bash -ec '
  apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq git build-essential curl >/dev/null
  
  # Detex
  git clone https://github.com/hglm/detex.git /detex
  cd /detex
  sed -i "s/LIBRARY_CONFIGURATION = STATIC/LIBRARY_CONFIGURATION = SHARED/" Makefile.conf
  make -j4
  cp libdetex.so* /out/libdetex.so
  cd /
  
  # zlib-ng2
  curl -fsSL https://github.com/NotOfficer/Zlib-ng.NET/releases/download/1.0.0/libz-ng.so.gz | gunzip > /out/libz-ng.so || true
  
  # Oodle
  curl -fsSL https://github.com/working-title-41/go-oodle/releases/download/v1.0.0/liboo2corelinux64.so.9 -o /out/liboo2corelinux64.so.9 || true
'


echo "Installed tools in $TOOLS:"
ls -l "$TOOLS" "$TOOLS/cue-cli"

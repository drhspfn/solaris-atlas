FROM mcr.microsoft.com/dotnet/sdk:10.0 AS tools-builder
RUN apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq git cmake clang build-essential curl unzip >/dev/null

WORKDIR /tools
ENV TOOLS=/tools

# Ensure .NET 10 SDK is explicitly installed, bypassing broken base image tags
RUN curl -sSL https://dot.net/v1/dotnet-install.sh | bash /dev/stdin -c 10.0
ENV PATH="/root/.dotnet:${PATH}"

# FModelCLI
RUN git clone --recursive https://github.com/Herselfta/FModelCLI.git /src/fmodel && \
    cd /src/fmodel && git checkout ad969ec899a235b4b5b7706df9244112233dd7de && \
    dotnet publish FModelCLI/FModelCLI.csproj -c Release -r linux-x64 --self-contained true -p:PublishReadyToRun=false -o $TOOLS/fmodelcli

# CUE4Parse.CLI
RUN git clone --recursive https://github.com/joric/CUE4Parse.CLI.git /src/cue-cli && \
    cd /src/cue-cli && git checkout master && \
    git config user.email "build@example.com" && git config user.name "build" && \
    git remote add upstream https://github.com/FabianFG/CUE4Parse.git && \
    git fetch upstream && git merge upstream/master -m "merge" && \
    sed -i "s/return fullPath.Replace(.*);/return fullPath;/g" CUE4Parse-Conversion/ExportSession.cs && \
    sed -i 's/if (!provider.TryLoadPackage(package, out var pkg))/CUE4Parse.UE4.Assets.IPackage pkg = null; try { pkg = provider.LoadPackage(package); } catch(System.Exception ex) { System.Console.Error.WriteLine($"Load Exception: {ex}"); } if (pkg == null)/g' CUE4Parse.CLI/Program.cs && \
    dotnet add CUE4Parse.CLI/CUE4Parse.CLI.csproj package SkiaSharp.NativeAssets.Linux.NoDependencies && \
    dotnet publish CUE4Parse.CLI/CUE4Parse.CLI.csproj -c Release -r linux-x64 --self-contained true -p:PublishReadyToRun=false -o $TOOLS/cue-cli && \
    mv $TOOLS/cue-cli/CUE4Parse.CLI $TOOLS/cue-cli/cue4parse

# vgmstream
RUN curl -fsSL https://github.com/vgmstream/vgmstream/releases/latest/download/vgmstream-linux.zip -o /tmp/vgmstream.zip && \
    unzip /tmp/vgmstream.zip -d /tmp/vgmstream && \
    install -m 755 $(find /tmp/vgmstream -type f -name vgmstream-cli | head -n1) $TOOLS/vgmstream-cli

# Detex
RUN git clone https://github.com/hglm/detex.git /src/detex && \
    cd /src/detex && sed -i "s/LIBRARY_CONFIGURATION = STATIC/LIBRARY_CONFIGURATION = SHARED/" Makefile.conf && \
    sed -i "s/DST_SHARED/DETEX_SHARED/g" Makefile && \
    make -j4 && cp libdetex.so.0.1.2 $TOOLS/libdetex.so && \
    nm -D $TOOLS/libdetex.so | grep detexDecompressTextureLinear

# zlib-ng2 & oo2core
# zlib-ng2 & oo2core (May 404, so we allow them to fail and fallback to host mounts if needed)
RUN curl -fsSL https://github.com/NotOfficer/Zlib-ng.NET/releases/download/1.0.0/libz-ng.so.gz | gunzip > $TOOLS/libz-ng.so || true; \
    curl -fsSL https://github.com/working-title-41/go-oodle/releases/download/v1.0.0/liboo2corelinux64.so.9 -o $TOOLS/liboo2corelinux64.so.9 || true

FROM ghcr.io/astral-sh/uv:0.11.6 AS uv
FROM python:3.12-slim AS runtime
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates ffmpeg strace \
    && rm -rf /var/lib/apt/lists/*
COPY --from=tools-builder /tools /app/tools
COPY --from=uv /uv /uvx /bin/
ENV UV_PROJECT_ENVIRONMENT=/opt/venv PATH="/opt/venv/bin:$PATH" PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app/packages/worker
COPY packages/server/pyproject.toml /app/packages/server/pyproject.toml
COPY packages/server/src /app/packages/server/src
COPY packages/worker/pyproject.toml packages/worker/uv.lock ./
COPY packages/worker/src ./src
RUN uv sync --frozen --no-dev
RUN mkdir -p /var/lib/wuwa-worker && useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app /var/lib/wuwa-worker
USER appuser
CMD ["wuwa-story-worker", "run"]

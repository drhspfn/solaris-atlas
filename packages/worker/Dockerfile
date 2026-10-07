FROM mcr.microsoft.com/dotnet/sdk:9.0 AS tools-builder
RUN apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq git cmake clang build-essential curl unzip >/dev/null

WORKDIR /tools
ENV TOOLS=/tools

# FModelCLI
RUN git clone --recursive https://github.com/Herselfta/FModelCLI.git /src/fmodel && \
    cd /src/fmodel && git checkout ad969ec899a235b4b5b7706df9244112233dd7de && \
    dotnet publish FModelCLI/FModelCLI.csproj -c Release -r linux-x64 --self-contained true -p:PublishReadyToRun=false -o $TOOLS/fmodelcli

# CUE4Parse.CLI
RUN git clone --recursive https://github.com/joric/CUE4Parse.CLI.git /src/cue-cli && \
    cd /src/cue-cli && git checkout cli-0.2.0 && \
    sed -i 's/net10.0/net9.0/g' Directory.Build.props && \
    dotnet publish CUE4Parse.CLI/CUE4Parse.CLI.csproj -c Release -r linux-x64 --self-contained true -p:PublishReadyToRun=false -o $TOOLS/cue-cli && \
    mv $TOOLS/cue-cli/CUE4Parse.CLI $TOOLS/cue-cli/cue4parse

# vgmstream
RUN curl -fsSL https://github.com/vgmstream/vgmstream/releases/latest/download/vgmstream-linux.zip -o /tmp/vgmstream.zip && \
    unzip /tmp/vgmstream.zip -d /tmp/vgmstream && \
    install -m 755 $(find /tmp/vgmstream -type f -name vgmstream-cli | head -n1) $TOOLS/vgmstream-cli

# Detex
RUN git clone https://github.com/hglm/detex.git /src/detex && \
    cd /src/detex && sed -i "s/LIBRARY_CONFIGURATION = STATIC/LIBRARY_CONFIGURATION = SHARED/" Makefile.conf && \
    make -j4 && cp libdetex.so* $TOOLS/libdetex.so

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

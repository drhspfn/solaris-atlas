FROM mcr.microsoft.com/dotnet/sdk:10.0.401 AS tools-builder
RUN apt-get update && apt-get install -y --no-install-recommends \
    git cmake clang build-essential curl unzip python3 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /tools

# Pin both sides of the CLI's CUE4Parse integration; never merge moving master branches.
RUN git clone https://github.com/Herselfta/FModelCLI.git /src/fmodel \
    && git -C /src/fmodel checkout ad969ec899a235b4b5b7706df9244112233dd7de \
    && git -C /src/fmodel submodule update --init --recursive \
    && git clone https://github.com/joric/CUE4Parse.CLI.git /src/cue-cli \
    && git -C /src/cue-cli checkout ab447bbcd4e11f426c41c417cacd937fab71c789 \
    && git -C /src/cue-cli fetch https://github.com/FabianFG/CUE4Parse.git cdef68791d0830d080e42477c343d873881ad52f \
    && git -C /src/cue-cli -c user.email=build@example.com -c user.name=build merge --no-edit FETCH_HEAD \
    && git -C /src/cue-cli submodule update --init --recursive

COPY packages/worker/docker /build
RUN python3 /build/patch_tools.py /src/fmodel /src/cue-cli \
    && dotnet publish /src/fmodel/FModelCLI/FModelCLI.csproj -c Release -r linux-x64 \
       --self-contained true -p:PublishReadyToRun=false -p:PublishSingleFile=false \
       -p:PublishTrimmed=false -o /tools/fmodelcli \
    && dotnet publish /src/cue-cli/CUE4Parse.CLI/CUE4Parse.CLI.csproj -c Release -r linux-x64 \
       --self-contained true -p:PublishReadyToRun=false -p:PublishSingleFile=false \
       -p:PublishTrimmed=false -o /tools/cue-cli \
    && mv /tools/cue-cli/CUE4Parse.CLI /tools/cue-cli/cue4parse

# Required native libraries. A missing download or symbol must fail the image build.
RUN curl -fSL --retry 3 https://github.com/WorkingRobot/OodleUE/releases/download/2026-06-04-1357/gcc-x64-release.zip -o /tmp/oodle.zip \
    && echo '458c66945ea6124d4f6fcf64cec5601ab230f61f6677a6ecab6cca621a9d0e11  /tmp/oodle.zip' | sha256sum -c - \
    && unzip -p /tmp/oodle.zip lib/liboodle-data-shared.so > /tools/liboodle-data-shared.so \
    && git clone https://github.com/zlib-ng/zlib-ng.git /src/zlib-ng \
    && git -C /src/zlib-ng checkout 860e4cff7917d93f54f5d7f0bc1d0e8b1a3cb988 \
    && cmake -S /src/zlib-ng -B /src/zlib-ng/build -DCMAKE_BUILD_TYPE=Release \
       -DZLIB_COMPAT=OFF -DZLIB_ENABLE_TESTS=OFF -DBUILD_SHARED_LIBS=ON \
    && cmake --build /src/zlib-ng/build --parallel 4 \
    && cp -L /src/zlib-ng/build/libz-ng.so /tools/libz-ng.so \
    && git clone https://github.com/hglm/detex.git /src/detex \
    && git -C /src/detex checkout cab11584d9be140602a66fd9a88ef0b99f08a97a \
    && sed -i 's/LIBRARY_CONFIGURATION = STATIC/LIBRARY_CONFIGURATION = SHARED/' /src/detex/Makefile.conf \
    && sed -i 's/DST_SHARED/DETEX_SHARED/g' /src/detex/Makefile \
    && make -C /src/detex -j4 \
    && cp /src/detex/libdetex.so.0.1.2 /tools/libdetex.so \
    && nm -D /tools/libdetex.so | grep ' T detexDecompressTextureLinear$' \
    && nm -D /tools/libz-ng.so | grep -E ' T zng_uncompress(@@[^ ]+)?$' \
    && nm -D /tools/liboodle-data-shared.so | grep ' T OodleLZ_Decompress$' \
    && python3 /build/native_probe.py /tools \
    && for tool in fmodelcli cue-cli; do \
         mkdir -p /tools/$tool/.data; \
         cp /tools/liboodle-data-shared.so /tools/libz-ng.so /tools/oodle-probe.bin /tools/$tool/.data/; \
         cp /tools/libdetex.so /tools/$tool/.data/Detex.dll; \
       done

RUN curl -fSL --retry 3 https://github.com/vgmstream/vgmstream/releases/download/r2117/vgmstream-linux.zip -o /tmp/vgmstream.zip \
    && echo '2f98c77f756079f63fbd119939067f1ed461d77e70993bc4cc372736d859c84a  /tmp/vgmstream.zip' | sha256sum -c - \
    && unzip /tmp/vgmstream.zip -d /tmp/vgmstream \
    && install -m 755 /tmp/vgmstream/vgmstream-cli /tools/vgmstream-cli \
    && cd /tools && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS

FROM ghcr.io/astral-sh/uv:0.11.6 AS uv
FROM python:3.12-slim AS runtime
RUN apt-get update && apt-get install -y --no-install-recommends \
    git ca-certificates ffmpeg libstdc++6 libgomp1 libssl3 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=tools-builder /tools /app/tools
COPY --from=uv /uv /uvx /bin/
ENV UV_PROJECT_ENVIRONMENT=/opt/venv PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=1 LD_LIBRARY_PATH=/app/tools \
    WUWA_TOOLS_DIR=/app/tools
WORKDIR /app/packages/worker
COPY packages/server/pyproject.toml /app/packages/server/pyproject.toml
COPY packages/server/src /app/packages/server/src
COPY packages/worker/pyproject.toml packages/worker/uv.lock ./
COPY packages/worker/src ./src
RUN uv sync --frozen --no-dev
RUN mkdir -p /var/lib/wuwa-worker && useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /var/lib/wuwa-worker
USER appuser
# Exercise actual managed/native calls in the final OS as the runtime user, offline.
RUN --network=none /app/tools/fmodelcli/FModelCLI --check-native-libs \
    && /app/tools/cue-cli/cue4parse --check-native-libs \
    && python -c "import wave; w=wave.open('/tmp/audio-probe.wav','wb'); w.setparams((1,2,8000,0,'NONE','not compressed')); w.writeframes(b'\x00\x00'*8000); w.close()" \
    && /app/tools/vgmstream-cli -o /tmp/audio-decoded.wav /tmp/audio-probe.wav \
    && python -c "import wave; w=wave.open('/tmp/audio-decoded.wav'); assert w.getnframes()==8000; w.close()" \
    && rm /tmp/audio-probe.wav /tmp/audio-decoded.wav
CMD ["wuwa-story-worker", "run"]

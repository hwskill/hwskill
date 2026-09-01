FROM node:22.22.0-bookworm-slim@sha256:dd9d21971ec4395903fa6143c2b9267d048ae01ca6d3ea96f16cb30df6187d94

LABEL org.hwskill.test.schema="1" \
      org.opencontainers.image.title="hwskill standard test runner" \
      org.opencontainers.image.version="0.1.0"

ENV PATH="/opt/hwskill-venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HWSKILL_REGISTRY_ROOT=/registry

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-venv git ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && npm install --global --omit=dev --no-audit --no-fund \
       "@openai/codex@0.147.0" \
       "@anthropic-ai/claude-code@2.1.141" \
       "opencode-ai@1.14.48" \
    && npm cache clean --force

RUN apt-get update \
    && apt-get install -y --no-install-recommends jq \
    && rm -rf /var/lib/apt/lists/*

COPY docker/test/requirements.lock /opt/hwskill-build/requirements.lock
RUN python3 -m venv /opt/hwskill-venv \
    && /opt/hwskill-venv/bin/pip install --disable-pip-version-check --no-cache-dir \
       --requirement /opt/hwskill-build/requirements.lock

COPY pyproject.toml /opt/hwskill-source/pyproject.toml
COPY src /opt/hwskill-source/src
RUN /opt/hwskill-venv/bin/pip install --disable-pip-version-check --no-cache-dir \
      --no-build-isolation --no-deps /opt/hwskill-source \
    && rm -rf /opt/hwskill-build /opt/hwskill-source

COPY README.md install.sh pyproject.toml .gitignore .dockerignore /opt/hwskills/
COPY examples /opt/hwskills/examples
COPY profiles /opt/hwskills/profiles
COPY registry /opt/hwskills/registry
COPY scripts /opt/hwskills/scripts
COPY skills-src /opt/hwskills/skills-src
COPY sources /opt/hwskills/sources
COPY src /opt/hwskills/src
COPY tests /opt/hwskills/tests
COPY docker/test /opt/hwskills/docker/test
COPY docker/test/entrypoint.sh /usr/local/bin/hwskill-test-entrypoint
RUN chmod 0555 /usr/local/bin/hwskill-test-entrypoint \
    && mkdir -p /registry /tests /artifacts /workspace /credentials/codex /credentials/claude-code /credentials/opencode

WORKDIR /workspace
ENTRYPOINT ["/usr/local/bin/hwskill-test-entrypoint"]

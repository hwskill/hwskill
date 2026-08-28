FROM node:22-bookworm-slim

ARG CODEX_VERSION=0.147.0
ARG CLAUDE_CODE_VERSION=2.1.141
ARG OPENCODE_VERSION=1.14.48

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-pip python3-venv git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN npm install --global \
    "@openai/codex@${CODEX_VERSION}" \
    "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}" \
    "opencode-ai@${OPENCODE_VERSION}"

COPY . /opt/hwskills
RUN python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install /opt/hwskills \
    && useradd --create-home --uid 10001 demo \
    && cp -a /opt/hwskills/examples/codex-demo /workspace-demo \
    && chown -R demo:demo /workspace-demo /home/demo

ENV PATH="/opt/venv/bin:${PATH}"
ENV HWSKILL_REGISTRY_ROOT=/opt/hwskills
ENV HWSKILL_AUDIT_PATH=/tmp/hwskill-audit.jsonl
WORKDIR /workspace-demo
USER demo

ENTRYPOINT ["/opt/hwskills/docker/entrypoint.sh"]

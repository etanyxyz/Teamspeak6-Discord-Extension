# tsdiscord — TeamSpeak 6 -> Discord bridge
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

RUN useradd -m bridge
USER bridge

# Reads configuration from environment (see .env / docker-compose.yml):
#   TS_HOST, TS_SSH_PORT, TS_QUERY_USER, TS_QUERY_PASSWORD,
#   DISCORD_WEBHOOK_URL, BRIDGE_NICKNAME, TS_BRIDGE_CHANNEL,
#   FORWARD_PRIVATE_MESSAGES, IGNORE_CLIENT_UIDS, LOG_LEVEL
ENTRYPOINT ["tsdiscord"]

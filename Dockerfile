# MarketDesk — the always-on desk, as one container.
#
# WHAT THIS RUNS
# `desk daemon`: one process that polls the chat, checks the watched levels
# every few seconds, and runs a full reading every 15 minutes. That is the shape
# a real host wants. On GitHub Actions the desk had to be two loops that never
# spoke (a runner is a fresh VM, so nothing stays resident); here it is one.
#
# STATE MUST OUTLIVE THE CONTAINER
# The alert ledger and the SQLite store live under DESK_HOME. Point it at a
# mounted volume, or every restart forgets which signals were already sent and
# re-sends them. The image does NOT bake in a volume: a volume is a deployment
# decision, and silently losing the ledger is worse than being asked for one.
#
#   docker run -d --name marketdesk --restart unless-stopped \
#     -e TELEGRAM_BOT_TOKEN=... -e TELEGRAM_CHAT_ID=... -e GROQ_API_KEY=... \
#     -v marketdesk-data:/data -e DESK_HOME=/data marketdesk
#
# The desk is stdlib-only, so there is nothing to install beyond the package
# itself -- no wheels, no lockfile, no build toolchain in the final image.

FROM python:3.13-slim

# Unbuffered output, so a log line is visible the moment it happens rather than
# when a pipe flushes -- the whole point of a daemon is that you can watch it.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DESK_HOME=/data

WORKDIR /app

# Only what the package needs. `tests/` and the docs are not copied: a smaller
# image is a smaller surface, and a container has no use for them.
COPY marketdesk/pyproject.toml /app/marketdesk/pyproject.toml
COPY marketdesk/desk /app/marketdesk/desk

RUN pip install --no-cache-dir -e /app/marketdesk

# A named user, so the process does not run as root. It owns DESK_HOME, because
# the desk writes its ledger and database there and a read-only home would turn
# every alert into a failed write.
RUN useradd --create-home --uid 10001 desk \
    && mkdir -p /data \
    && chown -R desk:desk /data /app
USER desk

# Liveness, not readiness: the desk is silent by design, so "is it alive" cannot
# be answered by asking for a signal. A heartbeat file the daemon touches would
# be one more thing to keep in sync; a trivial import check proves the image is
# runnable, and the container's own logs say whether the loop is turning.
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import desk.daemon, desk.cli" || exit 1

# `exec`-form entrypoint: signals (SIGTERM on stop) reach the daemon, so it can
# finish the tick instead of being killed mid-write.
#
# `--web` serves an inbound endpoint: Telegram PUSHES updates (a press is
# answered the instant it happens) and `/health` is inbound traffic, which is
# what stops a free host from sleeping. Without it the desk long-polls, which is
# entirely outbound and would never wake a sleeping host.
ENV DESK_PORT=8080
EXPOSE 8080
ENTRYPOINT ["python", "-m", "desk.cli", "daemon", "--web"]

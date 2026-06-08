FROM debian:12-slim

ENV TASK_MANAGER_HOST=0.0.0.0 \
    TASK_MANAGER_PORT=7860 \
    TASK_TTYD_HOST=0.0.0.0 \
    TASK_PORT_START=7700 \
    TASK_PORT_END=7799 \
    TTYD_VERSION=1.7.7 \
    TASK_MANAGER_BASE_DIR=/workspace \
    TTYD_BIN=/workspace/tools/ttyd/1.7.7/ttyd

CMD ["sh", "-c", "exec nsenter --target 1 --mount --uts --ipc --net --pid -- python3 -u \"${TASK_MANAGER_BASE_DIR}/server.py\""]

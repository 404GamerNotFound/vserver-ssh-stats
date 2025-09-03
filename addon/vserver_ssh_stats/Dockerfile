ARG BUILD_FROM=ghcr.io/hassio-addons/base:16.2.2
FROM ${BUILD_FROM}

RUN apk add --no-cache \
    python3 py3-pip py3-setuptools python3-dev build-base \
    rust cargo libffi-dev openssl-dev libsodium-dev \
    && python3 -m pip install --no-cache-dir --break-system-packages \
        wheel paramiko paho-mqtt asyncssh websockets

COPY run.sh /run.sh
COPY app /app
RUN chmod a+x /run.sh
ENTRYPOINT ["/init"]
CMD ["/run.sh"]

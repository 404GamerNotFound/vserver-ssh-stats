ARG BUILD_FROM=ghcr.io/hassio-addons/base:16.2.2
FROM ${BUILD_FROM}

RUN apk add --no-cache python3 py3-pip jq \
 && pip3 install --no-cache-dir paramiko paho-mqtt

COPY run.sh /run.sh
COPY app /app
RUN chmod a+x /run.sh
CMD ["/run.sh"]

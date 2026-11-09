ARG ASTRBOT_BASE=local/astrbot-shrink:4.29.4
FROM ${ASTRBOT_BASE}
COPY requirements.txt /tmp/shrink-requirements.txt
RUN python -m pip install --no-cache-dir -r /tmp/shrink-requirements.txt

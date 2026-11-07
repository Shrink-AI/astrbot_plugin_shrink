FROM soulter/astrbot:v4.29.3
COPY requirements.txt /tmp/shrink-requirements.txt
RUN python -m pip install --no-cache-dir -r /tmp/shrink-requirements.txt

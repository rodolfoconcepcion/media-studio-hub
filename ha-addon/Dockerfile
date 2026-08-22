ARG BUILD_FROM
FROM ${BUILD_FROM}

ENV LANG=C.UTF-8

# Install system dependencies & ffmpeg
RUN apk add --no-cache \
    python3 \
    py3-pip \
    py3-setuptools \
    ffmpeg \
    bash \
    curl \
    jq \
    git

# Install Python audio libraries & CLI engines
RUN pip3 install --no-cache-dir --break-system-packages \
    yt-dlp \
    spotdl \
    mutagen

WORKDIR /app

# Copy server, templates and scripts
COPY media_server.py /app/media_server.py
COPY templates /app/templates
COPY scripts /app/scripts
COPY run.sh /run.sh
RUN chmod +x /run.sh /app/scripts/*.sh

EXPOSE 8888

CMD [ "/run.sh" ]

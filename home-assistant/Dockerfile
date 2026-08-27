ARG BUILD_FROM
FROM ${BUILD_FROM}

# The Supervisor's build context is this directory (home-assistant/), which is
# why the Protocol v1 codec is vendored into matrix_studio/vendor/ rather than
# referenced at ../protocol/ — nothing above this directory exists here.

ENV LANG=C.UTF-8 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt ./
# --only-binary keeps a missing wheel loud and fast instead of silently
# starting a multi-hour source build of numpy on a Raspberry Pi.
RUN pip install --no-cache-dir --only-binary=:all: -r requirements.txt

COPY matrix_studio/ ./matrix_studio/
COPY example_scenes/ ./example_scenes/
COPY run.sh ./
RUN chmod a+x /app/run.sh

LABEL \
  io.hass.name="Matrix Studio" \
  io.hass.description="Generative-art frame server for ESP32-S3 HUB75 LED matrices" \
  io.hass.type="addon" \
  io.hass.version="0.1.6"

CMD ["/app/run.sh"]

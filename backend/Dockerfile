FROM python:3.12-slim

ARG APP_GIT_SHA=unknown
ARG APP_IMAGE_TAG=unknown
LABEL org.opencontainers.image.revision=$APP_GIT_SHA \
      org.opencontainers.image.version=$APP_IMAGE_TAG \
      com.quantx.decision-engine="active_drive_v2"

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV APP_GIT_SHA=$APP_GIT_SHA
ENV APP_IMAGE_TAG=$APP_IMAGE_TAG

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY app ./app

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

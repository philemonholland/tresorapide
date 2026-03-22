FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        build-essential \
        libcairo2-dev \
        pkg-config \
        poppler-utils \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r /app/requirements.txt

COPY . /app

RUN sed -i 's/\r$//' /app/scripts/docker-start.sh \
    && chmod +x /app/scripts/docker-start.sh

EXPOSE 8000

ENTRYPOINT ["/app/scripts/docker-start.sh"]

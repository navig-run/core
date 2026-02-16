FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv/app
COPY requirements.txt /srv/app/requirements.txt
RUN pip install --no-cache-dir -r /srv/app/requirements.txt

COPY app /srv/app/app
COPY contracts /srv/app/contracts
COPY migrations /srv/app/migrations
COPY init-db.sql /srv/app/init-db.sql

ENV PYTHONPATH=/srv/app

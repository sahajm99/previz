# Magic Hour, one container. Built from backend/ as the context.
#
# An explicit Dockerfile rather than buildpacks, because buildpacks guess the
# entrypoint and today is not a day to debug a guess. It also lets the committed
# demo_cache into the image, which is what makes the deployed app show a full
# board on first load without generating anything.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Requirements first so a code change does not reinstall the dependency layer.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY demo_cache ./demo_cache

# Cloud Run injects PORT. Uvicorn must bind 0.0.0.0 or the container looks dead to
# the health check. One worker on purpose: the store is in process memory, so a
# second worker would serve a different story from the first.
ENV PORT=8080
EXPOSE 8080
CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers 1

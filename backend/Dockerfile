# Magic Hour, one container.
#
# BUILD CONTEXT IS THE REPO ROOT, not backend/:
#     docker build -f backend/Dockerfile .
#     gcloud run deploy --source .          (from the repo root)
#
# It has to be the root because the app reads data/seed/character_questions.json,
# which lives outside backend/. Building from backend/ produces an image that
# boots, serves the UI, and then 404s every /api route, because main.py mounts the
# API inside a try/except and the missing questions file makes that import fail.
# That failure mode is quiet, so it is worth the comment.
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
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app
COPY backend/demo_cache ./demo_cache
# The 100 questions and the seed fixtures. store.DATA looks for a data/ directory
# beside backend/ and then beside app/, so this flatter layout resolves without a
# path shim.
COPY data ./data

# Cloud Run injects PORT. Uvicorn must bind 0.0.0.0 or the container looks dead to
# the health check. One worker on purpose: the store is in process memory, so a
# second worker would serve a different story from the first.
ENV PORT=8080
EXPOSE 8080
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers 1"]

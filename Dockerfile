# Optional convenience image — running with plain `python run.py` (see
# README) is the supported path; this is just a one-command alternative.
FROM python:3.12-slim

# git is required at runtime: history parsing shells out to `git log`.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000
# Cloned repositories, uploads and rat.db live here — mount a volume to
# keep them across container restarts.
VOLUME ["/app/data"]

CMD ["python", "run.py", "--host", "0.0.0.0", "--port", "8000"]

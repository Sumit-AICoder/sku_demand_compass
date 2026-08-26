FROM python:3.12-slim
WORKDIR /app

COPY requirements-serve.txt .
RUN pip install --no-cache-dir -r requirements-serve.txt

# Code is baked into the image. data/curated and data/marts (~1.1GB) are NOT -- they
# exceed Railway's upload limit as a build artifact, so a single persistent volume is
# mounted at /app/data instead, and the pre-generated parquet files are uploaded onto
# it separately (see DEPLOY.md). The volume mount shadows whatever the image had at
# /app/data, so the boundary geojson the pipeline needed to BUILD this data is not
# required at serving time and is not copied in here.
COPY pipeline/ pipeline/
COPY api/ api/

ENV PYTHONUNBUFFERED=1
EXPOSE 8000
CMD uvicorn api.main:app --host 0.0.0.0 --port ${PORT:-8000}

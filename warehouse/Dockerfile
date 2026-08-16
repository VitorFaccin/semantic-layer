# Tiny image whose only job is to build the local DuckDB warehouse before Cube starts.
# It exists so `docker compose up` is enough on a fresh clone: no cloud project, no credentials,
# no manual load. Kept separate from the Cube image so its pip layer caches independently.
FROM python:3.11-slim

WORKDIR /repo
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# The repository is bind-mounted at run time; the generated .duckdb lands back on the host.
CMD ["python", "warehouse/seed/load_duckdb.py"]

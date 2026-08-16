# MCP Gateway image — separate from the Cube all-in-one image (own lifecycle/runtime).
# PINNED base (repo policy: never :latest).
FROM python:3.12-slim

WORKDIR /app

# Deps first (layer cache).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

EXPOSE 8000

# Streamable HTTP MCP served by FastAPI/uvicorn.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

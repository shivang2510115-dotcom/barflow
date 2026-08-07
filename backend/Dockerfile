FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install deps first so layer caching survives source edits
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# emergentintegrations is vendored in this repo, not on PyPI — it ships with the source below
COPY . .

# Render/Fly inject PORT; default to 8000 for local `docker run`
ENV PORT=8000
EXPOSE 8000

CMD ["sh", "-c", "uvicorn server:app --host 0.0.0.0 --port ${PORT}"]

# Account Setup web server: FastAPI + headless Chromium (Playwright).
# The Playwright image already has Chromium and its system libraries.
FROM mcr.microsoft.com/playwright/python:v1.54.0-jammy

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py ./
# config.py (with local secrets) is .dockerignored; the image gets the env-only template.
COPY config.example.py config.py
COPY static ./static

# Jobs DB and the persistent browser profile live on the /data volume.
ENV DATA_DIR=/data \
    BROWSER_PROFILE=/data/browser_profile \
    HEADLESS=true \
    PYTHONUNBUFFERED=1
VOLUME /data
EXPOSE 8000

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]

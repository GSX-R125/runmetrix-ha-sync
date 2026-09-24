FROM python:3.12-bookworm

ENV PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    TZ=Asia/Tokyo

RUN pip install --no-cache-dir playwright==1.63.0 \
    && python -m playwright install --with-deps chromium

WORKDIR /app
COPY runner.py /app/runner.py
CMD ["python", "/app/runner.py"]

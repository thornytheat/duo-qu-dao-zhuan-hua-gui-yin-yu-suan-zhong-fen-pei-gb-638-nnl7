FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MIXCREDIT_DB=/data/mixcredit.sqlite \
    MIXCREDIT_SAMPLE_DIR=/app/sample_data

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY docs ./docs
COPY sample_data ./sample_data
COPY pytest.ini ./
COPY tests ./tests

RUN mkdir -p /data

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

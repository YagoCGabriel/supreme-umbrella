FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=3000

WORKDIR /app

COPY app/requirements.txt app/requirements-prod.txt ./
RUN pip install --no-cache-dir -r requirements-prod.txt

COPY app/ ./

EXPOSE 3000
CMD ["gunicorn", "--workers", "2", "--threads", "4", "--timeout", "30", "--bind", "0.0.0.0:3000", "api:app"]

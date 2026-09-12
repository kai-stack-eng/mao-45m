# Build context is the repository root, so mao_45m is available.
FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir "psycopg[binary]>=3.1" typing_extensions

COPY mao_45m/ ./mao_45m/
COPY monitor/collector.py ./monitor/

CMD ["python", "monitor/collector.py"]

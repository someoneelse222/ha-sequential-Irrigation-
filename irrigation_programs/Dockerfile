FROM python:3.13-alpine
RUN pip install --no-cache-dir aiohttp==3.11.18 pyyaml==6.0.3 tzdata
WORKDIR /app
COPY app/ /app/
ENV PYTHONUNBUFFERED=1
CMD ["python3", "/app/main.py"]

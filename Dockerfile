FROM python:3.12-alpine

WORKDIR /app

RUN apk add --no-cache kubectl

COPY pyproject.toml README.md ./
COPY src ./src

RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir .

EXPOSE 8080

CMD ["uvicorn", "investigation_service.main:app", "--host", "0.0.0.0", "--port", "8080"]

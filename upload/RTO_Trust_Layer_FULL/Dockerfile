FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY src ./src
COPY scripts ./scripts
COPY dashboard ./dashboard
COPY tests ./tests
RUN mkdir -p data/raw out
COPY data/raw/cod_orders.csv data/raw/cod_orders.csv
ENV RTO_SCORER_KEYS=change-me-scorer \
    RTO_ADMIN_KEYS=change-me-admin \
    RTO_AUDIT_SALT=rotate-in-prod \
    RTO_MANDATE_SECRET=rotate-in-prod
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request;urllib.request.urlopen('http://localhost:8000/health')"
CMD ["uvicorn", "src.api.routes:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]

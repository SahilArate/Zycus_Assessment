
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY erp.py .
COPY main.py .
COPY bookable_payable/ ./bookable_payable/
COPY master_data/ ./master_data/
COPY AUTODRAFT_SCHEMA.md .
COPY sample_autodraft.json .

VOLUME ["/app/documents", "/app/output"]

ENTRYPOINT ["python", "main.py"]
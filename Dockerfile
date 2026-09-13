# Base image: matches the Python version this project was developed and tested
# against (3.11). Slim variant keeps the image small; PyMuPDF ships prebuilt
# wheels for this platform, so no extra system libraries are needed.
FROM python:3.11-slim

WORKDIR /app

# Dependencies first, so this layer is cached and doesn't reinstall on every
# code change — only when requirements.txt itself changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Everything the pipeline actually needs to run. Deliberately explicit (not
# `COPY . .`) so tests/, .git, .env, and local scratch files never end up
# inside the image.
COPY erp.py .
COPY main.py .
COPY bookable_payable/ ./bookable_payable/
COPY master_data/ ./master_data/
COPY AUTODRAFT_SCHEMA.md .
COPY sample_autodraft.json .

# documents/ (input) and output/ (results) are mounted at run time, not baked
# into the image. This means the same image can be re-run against a different
# or updated set of documents without rebuilding — exactly what "we will
# re-run it" in the brief implies should be possible.
VOLUME ["/app/documents", "/app/output"]

ENTRYPOINT ["python", "main.py"]
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN mkdir -p /app/logs
EXPOSE 8000
CMD ["sh","-c","python scripts/wait_for_db.py && python -m uvicorn app.main:app --host 0.0.0.0 --port 8000"]

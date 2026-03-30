FROM python:3.10.19-alpine
LABEL authors="Nico"

WORKDIR /app

EXPOSE 5000

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .
COPY templates ./templates
COPY csv_db .

VOLUME /app/csv_db

CMD ["python", "app.py"]
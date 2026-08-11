FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app/datagen/
RUN touch /app/datagen/__init__.py

ENTRYPOINT ["python", "-m", "datagen.simulator"]

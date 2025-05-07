# Gebruik een officiële Python runtime als parent image
FROM python:3.9-slim

# Zet de werkdirectory in de container
WORKDIR /app

# Kopieer de dependency file en installeer dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Kopieer de rest van de applicatiecode naar de werkdirectory
COPY . .

# Maak de poort waarop de app draait beschikbaar
EXPOSE 5000

# Definieer environment variabelen (optioneel, maar goede praktijk)
ENV FLASK_APP=app.py
ENV FLASK_RUN_HOST=0.0.0.0
# Voor productie is Gunicorn beter dan de Flask development server
CMD ["gunicorn", "-w", "4", "-b", "0.0.0.0:5000", "app:app"]

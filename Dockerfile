FROM python:3.13.2

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY application.py .
COPY cache.py .
COPY config.py .
COPY controllers.py .
COPY i18n.py .
COPY locales.csv .
COPY logger.py .
COPY routes.py .
COPY tmdb.py .
COPY utils.py .
COPY static ./static

ENV CACHE_DIR=/app/cache
VOLUME /app/cache

CMD ["python", "application.py"]
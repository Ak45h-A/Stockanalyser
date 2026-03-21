FROM python:3.11.9-slim

RUN useradd -m -u 1000 user
USER user
ENV PATH="/home/user/.local/bin:$PATH"

WORKDIR /app

COPY --chown=user requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=user . .

EXPOSE 7860
CMD ["gunicorn", "-w", "1", "-k", "gevent", "--worker-connections", "500", "--timeout", "120", "-b", "0.0.0.0:7860", "app:app"]
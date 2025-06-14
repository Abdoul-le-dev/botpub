FROM python:3.11-slim
WORKDIR /app
RUN python -m venv env
COPY . /app
RUN . env/bin/activate
RUN pip install -r requirements.txt
CMD ["python", "script.py"]
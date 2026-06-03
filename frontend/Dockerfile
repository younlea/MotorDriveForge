FROM dgx-antigravity-corp:v2

WORKDIR /app
USER root

# Python 의존성
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 소스 복사 (paste_component, .streamlit 포함)
COPY . .

ENV BACKEND_URL=http://backend:8000

EXPOSE 8501

HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=6 \
    CMD bash -c ':> /dev/tcp/127.0.0.1/8501' 2>/dev/null || exit 1

CMD ["streamlit", "run", "app.py", \
     "--server.port=8501", \
     "--server.address=0.0.0.0"]

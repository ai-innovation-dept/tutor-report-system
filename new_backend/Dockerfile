FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    fonts-ipafont-gothic \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
RUN pip install --no-cache-dir -e ".[dev]" 2>/dev/null || pip install --no-cache-dir \
    "fastapi==0.115.2" \
    "uvicorn[standard]>=0.27" \
    "sqlalchemy>=2.0" \
    "alembic>=1.13" \
    "psycopg[binary]>=3.1" \
    "pydantic>=2.6" \
    "pydantic-settings>=2.2" \
    "python-jose[cryptography]>=3.3" \
    "passlib[bcrypt]>=1.7" \
    "bcrypt==4.0.1" \
    "python-multipart>=0.0.9" \
    "jinja2>=3.1" \
    "starlette==0.40.0" \
    "aiosmtplib>=3.0" \
    "email-validator>=2.1" \
    "httpx>=0.27" \
    "reportlab>=4.2" \
    "pytest>=8.0" \
    "pytest-asyncio>=0.23"

COPY . .

ENV PDF_JP_FONT_PATH=/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf

CMD ["sh", "-c", "python -m alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8001"]

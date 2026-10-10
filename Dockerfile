FROM python:3.12-slim

WORKDIR /app

# 不装任何系统依赖：全部 Python 包都有 manylinux wheel，
# 而项目本身零 JS 运行时依赖（不需要 Node，签名都是纯算的）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN useradd -m appuser && chown -R appuser:appuser /app
USER appuser

ENV PYTHONUNBUFFERED=1

# 和本地一样，配置 main.py 顶部的 DEMO 后直接运行。
CMD ["python", "main.py"]

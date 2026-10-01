# Imagem oficial do Playwright com Python e Chromium/dependências Linux pré-instaladas
FROM mcr.microsoft.com/playwright/python:v1.49.1-noble

WORKDIR /app

# Variáveis de ambiente de execução
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HEADLESS=true \
    PORT=10000 \
    HOST=0.0.0.0

# Copia e instala as dependências
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia o código-fonte da aplicação
COPY . .

# Garante a existência dos diretórios persistentes
RUN mkdir -p /app/data /app/sessao_whatsapp

# Porta padrão de escuta do serviço no Render
EXPOSE 10000

# Executa o orquestrador principal iniciando o servidor web FastAPI
CMD ["python", "main.py"]

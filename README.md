# Automação WhatsApp Web (Playwright)

Este projeto é uma automação em Python desenvolvida com a biblioteca **Playwright** para interagir com o WhatsApp Web. Ele permite salvar o estado de login do navegador em uma pasta local (`sessao_whatsapp`), evitando a necessidade de ler o QR Code a cada execução, e extrai as últimas mensagens de grupos ou comunidades selecionadas.

## 📂 Estrutura do Projeto

*   **`main.py`**: O arquivo orquestrador principal que fornece um menu interativo no terminal.
*   **`services/`**: Pasta contendo os módulos de execução:
    *   `coletor.py`: Responsável por abrir o WhatsApp Web e esperar o primeiro login (QR Code), salvando a sessão.
    *   `extrator.py`: Responsável por abrir o WhatsApp Web usando a sessão salva, buscar o grupo/comunidade e ler as últimas mensagens.
*   **`requirements.txt`**: Lista de dependências do Python para o projeto.
*   **`sessao_whatsapp/`**: Diretório criado dinamicamente que armazena os cookies e dados de login do navegador (perfil persistente).


## 🛠️ Pré-requisitos e Instalação

Antes de rodar, garanta que você tem o Python instalado em sua máquina.

0. **Criar a venv**
   `python -m venv .venv`

1. **Ativar o Ambiente Virtual (`.venv`)**:
   No Windows (PowerShell/CMD):
   ```powershell
   .\.venv\Scripts\activate
   ```

2. **Instalar as dependências**:
   ```bash
   pip install -r requirements.txt
   ```

3. **Instalar o navegador Chromium no Playwright**:
   ```bash
   playwright install chromium
   ```

## 🚀 Como Executar

### 1. Painel Web de Controle (Padrão)
Para iniciar a interface gráfica web da aplicação:
```bash
python main.py
```
Isso iniciará o servidor FastAPI e abrirá o painel em `http://localhost:8000`.

### 2. Modo Terminal Interativo (CLI)
Caso prefira o menu no terminal:
```bash
python main.py --cli
```

---

## 📦 Gerando o Executável Standalone (.exe)

Para empacotar a aplicação em um executável independente que o usuário final pode rodar sem precisar instalar Python:

1. **Instale o PyInstaller (já listado no requirements.txt):**
   ```bash
   pip install -r requirements.txt
   ```

2. **Gere a pasta de distribuição com o `.spec`:**
   ```bash
   pyinstaller whatsapp_insights.spec
   ```

3. **Resultado:**
   * A pasta gerada estará em `dist/WhatsAppInsights/`.
   * Basta compactar essa pasta em `.zip` e distribuir para o usuário final.
   * Ao executar `WhatsAppInsights.exe`, a aplicação iniciará o servidor local e abrirá o navegador automaticamente em `http://localhost:8000`.

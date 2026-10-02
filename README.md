# WhatsApp Insights LLM

Plataforma de inteligência de conversas, extração de dados e análise semântica para grupos e comunidades do WhatsApp, com suporte a modelos de linguagem avançados (**Anthropic Claude** e **Google Gemini**), arquitetura *Local-First* / *Cloud-Ready*, privacidade por padrão (*Privacy-by-Design* / LGPD) e painel web interativo.

---

## 🎯 Visão Geral

O **WhatsApp Insights LLM** coleta, organiza e analisa conversas de grupos e comunidades do WhatsApp de forma estruturada e segura. A aplicação permite transformar históricos volumosos de mensagens em diagnósticos estratégicos, métricas quantitativas de engajamento, detecção de sentimentos e relatórios executivos acionáveis.

### Principais Capacidades:
- **Coleta Automatizada e Resiliente:** Automação via Playwright com perfil de sessão persistente (login por QR Code uma única vez), catálogo automático de grupos e extração com janelas temporais flexíveis (horas, dias, semanas ou meses).
- **Importação de Exports Nativos:** Suporte a arquivos `.txt` e `.zip` exportados diretamente pelo WhatsApp (nos padrões pt-BR e internacionais).
- **Armazenamento Cumulativo e Multi-Banco:** Arquitetura dual com SQLite local e suporte a PostgreSQL / Neon Serverless em nuvem via SQLAlchemy, com deduplicação por chave única (`message_id`) e gestão de múltiplos workspaces/consultas.
- **Privacidade e Conformidade com LGPD:** Anonimização automática de remetentes (pseudônimos estáveis), mascaramento de telefones, e-mails, menções e links de redes sociais antes de qualquer processamento por IA.
- **Inteligência Artificial & LLMs:** Motor de IA com suporte à família Anthropic Claude (Claude 3.5 Sonnet, Claude 3.7 Sonnet, Claude 3 Haiku, Claude 3 Opus) e Google Gemini. Oferece chat analítico livre e diagnósticos temáticos pré-configurados.
- **Relatórios Executivos:** Geração de relatórios de saúde da comunidade baseados no framework de 3 eixos (Volume/Rotina, Moderação e Interação P2P), métricas de engajamento, alertas de dúvidas e termos críticos, exportáveis em HTML interativo.
- **Interface Web Moderna (SPA):** Painel web responsivo com streaming de logs em tempo real via Server-Sent Events (SSE).
- **Distribuição Flexível:** Executável desktop standalone (`.exe`), contêiner Docker ou execução via terminal/CLI.

---

## 🏛️ Arquitetura e Estrutura de Diretórios

O projeto segue os princípios de **Clean Architecture** e **SOLID**, mantendo a lógica de negócio desacoplada de detalhes de infraestrutura e interfaces:

```text
python_whatsapp_insights_llm/
│
├── services/                 # Regras de negócio e serviços modulares
│   ├── coletor.py            # Automação do WhatsApp Web e persistência de sessão
│   ├── extrator.py           # Extração incremental de mensagens com paginação
│   ├── parser_txt.py         # Leitura e parsing de arquivos .txt/.zip exportados
│   ├── anonymizer.py         # Anonimização de dados sensíveis e conformidade LGPD
│   ├── database.py           # Conexão e pooling multi-banco (SQLite / PostgreSQL)
│   ├── storage.py            # Persistência, deduplicação, consultas e backups
│   ├── cloud_sync.py         # Sincronização entre banco local e nuvem
│   ├── llm.py                # Integração com Claude e Gemini, prompts e chat analítico
│   ├── reports.py            # Geração de relatórios executivos e métricas
│   └── paths.py              # Gerenciador de caminhos do sistema e empacotamento
│
├── ui/                       # Interface de usuário
│   ├── server.py             # Servidor FastAPI, rotas REST e SSE para logs em tempo real
│   └── templates/
│       └── index.html        # Single Page Application (SPA)
│
├── data/                     # Armazenamento local (SQLite, consultas, backups e exports)
├── sessao_whatsapp/          # Perfil de autenticação persistente do navegador
├── scripts/                  # Scripts utilitários e de verificação
├── main.py                   # Orquestrador da aplicação (Web e CLI)
├── requirements.txt          # Dependências do Python
├── Dockerfile                # Configuração para implantação em contêiner
├── whatsapp_insights.spec    # Especificação do PyInstaller para build de executável
└── .env.example              # Modelo de variáveis de ambiente
```

---

## 🛠️ Pré-requisitos e Instalação

### 1. Criar e Ativar o Ambiente Virtual

```bash
# Criar o ambiente virtual
python -m venv .venv

# Ativar no Windows (PowerShell):
.\.venv\Scripts\Activate.ps1

# Ativar no Linux / macOS:
source .venv/bin/activate
```

### 2. Instalar as Dependências

```bash
pip install -r requirements.txt
```

### 3. Instalar o Navegador do Playwright

```bash
playwright install chromium
```

---

## ⚙️ Configuração de Variáveis de Ambiente

Crie um arquivo `.env` na raiz do projeto com base no arquivo `.env.example`:

```ini
# Chave da API Anthropic (obrigatória para uso de LLM Claude via backend)
ANTHROPIC_API_KEY=sua_chave_aqui
ANTHROPIC_MODEL=claude-3-5-sonnet-20241022

# Banco de Dados em Nuvem (Opcional - deixe em branco para usar SQLite local)
# Exemplo PostgreSQL / Neon: postgresql://user:pass@host/dbname?sslmode=require
DATABASE_URL=

# Configurações do Servidor Web
HOST=127.0.0.1
PORT=8000
```

> **Nota sobre Chaves de API:** A chave também pode ser informada dinamicamente na própria interface web, ficando armazenada com segurança no `localStorage` do navegador do usuário (*Bring Your Own Key - BYOK*).

---

## 🚀 Como Executar

### 1. Painel Web (Modo Padrão)

Inicie o servidor local da aplicação:

```bash
python main.py
```

O painel será inicializado e estará acessível em:
```
http://localhost:8000
```

Para abrir automaticamente o navegador padrão ao iniciar:
```bash
python main.py --open
```

### 2. Modo Terminal Interativo (CLI)

Caso queira executar coletas, exportações e operações diretamente via linha de comando:

```bash
python main.py --cli
```

---

## 📦 Funcionalidades Detalhadas

### 1. Coleta e Catálogo de Grupos
- **Login Persistente:** Escaneie o QR Code apenas na primeira utilização. Os cookies e credenciais de sessão são armazenados com segurança na pasta `sessao_whatsapp/`.
- **Sincronização do Catálogo:** Identifica automaticamente os grupos em que o usuário participa.
- **Janelas Temporais Customizáveis:** Permite extrair mensagens de intervalos específicos (ex.: últimas 24 horas, últimos 7 dias, último mês).

### 2. Importação e Exportação Universal
- **Formatos Aceitos na Importação:** Arquivos `.txt` ou `.zip` de conversas exportadas pelo WhatsApp móvel ou desktop.
- **Formatos de Exportação:** CSV, JSON e backup completo do banco de dados SQLite.

### 3. Anonimização e Privacidade (LGPD)
- Substituição de nomes reais por identificadores determinísticos (ex.: `membro-0001`).
- Redação automática de números de telefone (nacionais e internacionais), endereços de e-mail, perfis em redes sociais e menções nominais diretas.

### 4. Análise com Modelos de Linguagem
- **Chat Analítico:** Faça perguntas diretas sobre as discussões ocorridas no grupo.
- **Diagnósticos Especializados:**
  - Resumo Executivo e Principais Temas
  - Análise de Sentimento e Clima do Grupo
  - Dúvidas Recorrentes e Perguntas Frequentes (FAQs)
  - Identificação de Membros Destaque e Nível de Engajamento
  - Extração de Conquistas e Provas Sociais

---

## 🐳 Executando com Docker

Para construir e rodar a aplicação em um contêiner Docker:

```bash
# Construir a imagem Docker
docker build -t whatsapp-insights-llm .

# Executar o contêiner
docker run -p 8000:8000 --env-file .env whatsapp-insights-llm
```

---

## 🖥️ Gerando o Executável Standalone (.exe)

Para gerar uma distribuição independente para Windows (sem necessidade de Python instalado):

1. Execute o PyInstaller utilizando o arquivo de especificação:
   ```bash
   pyinstaller whatsapp_insights.spec
   ```

2. A pasta gerada estará disponível em:
   ```
   dist/WhatsAppInsights/
   ```

3. Ao executar `WhatsAppInsights.exe`, a aplicação inicializa o servidor local e abre a interface gráfica web no navegador padrão.

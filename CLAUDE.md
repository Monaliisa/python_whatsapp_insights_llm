# Diretrizes do Projeto — WhatsApp Insights LLM (CLAUDE.md)

Este documento define as diretrizes de desenvolvimento, visão arquitetural, padrões de código, comandos frequentes e regras de comportamento para assistentes de inteligência artificial neste repositório.

---

## 🎯 1. Visão Geral do Projeto

O **WhatsApp Insights LLM** é uma plataforma de inteligência de conversas, extração de dados e análise semântica para grupos e comunidades do WhatsApp.

### Objetivos Centrais:
1. **Coleta Resiliente:** Automação via Playwright com persistência de sessão de login (`sessao_whatsapp/`) e extração incremental com paginação por janelas de tempo.
2. **Ingestão Flexível:** Parsing e importação de exports nativos (`.txt` e `.zip`) do WhatsApp móvel e desktop.
3. **Persistência Cumulativa e Multi-Banco:** Armazenamento deduplicado (`message_id`) com suporte dual a SQLite local e PostgreSQL / Neon Serverless via SQLAlchemy.
4. **Privacidade e LGPD por Padrão (*Privacy-by-Design*):** Anonimização determinística de autores e mascaramento de dados sensíveis (telefones, e-mails, menções `@`, URLs sociais) antes do envio a LLMs.
5. **Inteligência Semântica:** Diagnósticos estruturados e chat analítico conversacional com **Anthropic Claude** (Claude 3.5 Sonnet, 3.7 Sonnet, Haiku, Opus) e suporte a Google Gemini.
6. **Relatórios Executivos:** Geração de diagnósticos de saúde da comunidade baseados no framework de 3 eixos (Volume/Rotina, Moderação e P2P) e exportação em HTML autônomo.

---

## 🏛️ 2. Arquitetura e Princípios de Engenharia

### 2.1. SOLID e Clean Architecture
- **Single Responsibility (SRP):** Cada serviço dentro de `services/` deve ter uma responsabilidade estrita e isolada.
- **Open/Closed (OCP):** Aberto para novos provedores de IA, novos bancos ou novos tipos de relatório; fechado para alterações em regras consolidadas.
- **Liskov Substitution (LSP):** Clientes de banco e de LLM devem respeitar interfaces previsíveis.
- **Interface Segregation (ISP):** Contratos de dados específicos (Pydantic / Dataclasses).
- **Dependency Inversion (DIP):** Módulos de alto nível não dependem de detalhes de implementação de I/O ou banco.

### 2.2. Modularização de Serviços (`services/`)
- Toda lógica de negócio, extração, transformação e inferência reside em `services/`.
- A camada de interface (`ui/server.py` e `ui/templates/index.html`) deve ser puramente apresentacional e de orquestração de rotas.
- O arquivo `main.py` atua exclusivamente como entrypoint e despachante (Web/CLI).

### 2.3. Local-First & Cloud-Ready
- Desenvolvimento focado em operação local sem dependências externas obrigatórias.
- Configuração inteiramente parametrizável via variáveis de ambiente (`.env`).
- Isolamento de estado em sessões, consultas e pastas de dados (`data/`).

---

## 💻 3. Comandos Úteis de Desenvolvimento

### Configuração do Ambiente
```bash
# Criar e ativar ambiente virtual
python -m venv .venv
.\.venv\Scripts\Activate.ps1   # Windows PowerShell
source .venv/bin/activate       # Linux/macOS

# Instalar dependências
pip install -r requirements.txt

# Instalar binários do Playwright
playwright install chromium
```

### Execução da Aplicação
```bash
# Iniciar painel Web (FastAPI) em http://localhost:8000
python main.py

# Iniciar painel Web e abrir o navegador automaticamente
python main.py --open

# Executar modo terminal interativo (CLI)
python main.py --cli
```

### Execução de Scripts de Teste e Validação
```bash
# Testar conectividade com banco de dados (SQLite / PostgreSQL)
python scripts/test_db_connection.py

# Validar isolamento de dados entre múltiplos grupos
python scripts/test_multi_group_isolation.py
```

### Empacotamento e Contêiner
```bash
# Build do executável desktop Standalone Windows (.exe)
pyinstaller whatsapp_insights.spec

# Build e execução com Docker
docker build -t whatsapp-insights-llm .
docker run -p 8000:8000 --env-file .env whatsapp-insights-llm
```

---

## 📂 4. Mapa da Estrutura de Diretórios

```text
python_whatsapp_insights_llm/
│
├── data/                     # Bancos SQLite, consultas ativas, backups e exports
├── services/                 # Serviços e lógica de negócio
│   ├── __init__.py
│   ├── coletor.py            # Automação Playwright e sessão WhatsApp
│   ├── extrator.py           # Extração incremental de mensagens do chat
│   ├── parser_txt.py         # Leitura e parsing de exports .txt/.zip
│   ├── anonymizer.py         # Sanitização de PII e pseudonimização LGPD
│   ├── database.py           # Conexão SQLAlchemy (SQLite / PostgreSQL)
│   ├── storage.py            # Persistência, deduplicação, backups e consultas
│   ├── cloud_sync.py         # Sincronização local <-> nuvem
│   ├── llm.py                # Integração com Anthropic Claude e prompts
│   ├── reports.py            # Relatórios mensais e métricas quantitativas
│   ├── models.py             # Modelos de dados e schemas
│   └── paths.py              # Gestão centralizada de paths e empacotamento
│
├── ui/                       # Interface de usuário
│   ├── server.py             # Servidor FastAPI, rotas REST e SSE
│   └── templates/
│       └── index.html        # Single Page Application (SPA)
│
├── sessao_whatsapp/          # Perfil de autenticação persistente do navegador
├── scripts/                  # Scripts de diagnóstico e validação
├── main.py                   # Orquestrador da aplicação
├── requirements.txt          # Dependências do projeto
├── Dockerfile                # Configuração para contêiner Docker
├── render.yaml               # Configuração para deploy em nuvem
├── whatsapp_insights.spec    # Configuração do PyInstaller
├── README.md                 # Documentação geral da aplicação
├── GEMINI.md                 # Diretrizes para assistentes
└── CLAUDE.md                 # Este documento de diretrizes
```

---

## 🛡️ 5. Regras de Comportamento para Assistentes de IA

1. **Abordagem Didática e Explicativa:**
   - Sempre detalhe o racional técnico por trás de cada refatoração, escolha de biblioteca ou padrão adotado.
2. **Guardião da Arquitetura:**
   - Se uma solicitação violar os princípios SOLID, Clean Architecture ou a separação de serviços, alerte explicitamente sobre o impacto e solicite dupla confirmação antes de prosseguir.
3. **Privacidade Estrita:**
   - Nunca exponha links remotos, URLs de repositórios privados ou credenciais reais em respostas e documentações.
4. **Testes Automatizados:**
   - Não priorize criação de suítes de testes desnecessárias sem solicitação direta; o foco está na arquitetura e nas funcionalidades de negócio.
5. **Controle de Versão e Commits:**
   - **NUNCA realize commits automaticamente.**
   - Ao concluir uma tarefa, sugira sempre uma mensagem de commit seguindo o padrão **Conventional Commits** com descrição e corpo **em português**:
     ```text
     <tipo>(<escopo opcional>): <descrição curta no imperativo em português>

     [corpo opcional explicando o porquê da alteração em português]
     ```

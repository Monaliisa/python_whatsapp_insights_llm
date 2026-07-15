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

Execute o orquestrador principal a partir da raiz do projeto:

```bash
python main.py
```

Você verá um menu no terminal:

```text
========================================
    ORQUESTRADOR DE AUTOMAÇÃO WHATSAPP
========================================
1. Iniciar Coletor (Fazer Login / Escanear QR Code)
2. Iniciar Extrator (Coletar mensagens do Grupo)
3. Sair
========================================
Escolha uma opção (1-3):
```

### Passo a Passo Recomendado:

1. **Primeira Execução (Login)**:
   * Escolha a **Opção 1** para abrir o navegador.
   * Escaneie o QR Code na tela com o seu celular.
   * Aguarde o script sincronizar e fechar sozinho (ele salvará a sessão em `sessao_whatsapp`).

2. **Uso no Dia a Dia (Extração)**:
   * Escolha a **Opção 2**.
   * O menu perguntará se você quer definir um grupo ou comunidade específicos, ou usar as configurações padrão (caso dê apenas `Enter`).
   * O navegador abrirá automaticamente logado e coletará as últimas 10 mensagens do grupo escolhido.

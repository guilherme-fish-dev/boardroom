# Boardroom

Chat local onde você cria agentes de IA (persona + modelo), agrupa em canais, e conversa
com eles usando `@menção`. As respostas são processadas uma de cada vez por uma fila
(descrição de imagem tem prioridade sobre resposta de agente), pra caber em GPUs com
VRAM limitada.

## Pré-requisitos

- Python 3.11+
- Um [llama-swap](https://github.com/mostlygeek/llama-swap) rodando localmente,
  proxyando um ou mais modelos llama.cpp (incluindo pelo menos um modelo de visão,
  se você quiser discutir imagens).

## Rodando localmente

```bash
python -m venv .venv
source .venv/Scripts/activate  # Windows Git Bash; use .venv\Scripts\Activate.ps1 no PowerShell
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

Abra `http://localhost:8000`.

### Voz (opcional)

Pra ouvir as mensagens em voz alta, baixe a voz em português do
[Piper](https://github.com/OHF-Voice/piper1-gpl) uma vez:

```bash
python -m piper.download_voices --download-dir ./data/piper-voices pt_BR-faber-medium
```

Sem esse modelo baixado, o botão de ouvir simplesmente não aparece — o resto do app
funciona normalmente.

Antes de usar:
1. Vá em **Configurações** e ajuste a URL base do llama-swap e o modelo de visão padrão
   (o alias exatamente como configurado no `llama-swap`).
2. Vá em **Agentes** e crie pelo menos um agente (nome sem espaços, persona, modelo).
3. Crie um grupo, adicione o agente como membro (via API `/api/groups/{id}/members` por
   enquanto — UI de gestão de membros é um próximo passo).
4. No canal, mande uma mensagem mencionando `@nome-do-agente`.

## Rodando os testes

```bash
pytest -v
```

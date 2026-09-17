# Scripts start.bat / stop.bat — controle do servidor local

Data: 2026-09-17
Status: Aprovado para planejamento

## Contexto e motivação

Hoje, rodar o Boardroom exige abrir um terminal, ativar o venv e rodar `uvicorn` manualmente, mantendo o terminal aberto e ocupado enquanto o servidor roda. Isso é fricção desnecessária pra um app local de uso diário. O usuário quer um jeito simples de subir e derrubar o servidor sem precisar gerenciar o processo manualmente.

## Objetivo

Dois scripts `.bat` na raiz do projeto:
- `start.bat`: sobe o servidor em background (processo desanexado, sem janela de console visível), libera o terminal imediatamente, e abre o navegador em `http://localhost:8000`.
- `stop.bat`: derruba o servidor iniciado por `start.bat`.

Fora de escopo: ícone de bandeja do sistema, instalação como serviço do Windows, suporte a múltiplas instâncias simultâneas, configuração de porta via argumento (fixo em 8000, igual ao README).

## Mecanismo de controle de processo

- `start.bat` usa PowerShell (`Start-Process -PassThru`) para iniciar `.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000` como processo oculto (`-WindowStyle Hidden`), com stdout/stderr redirecionados para `boardroom.log` e `boardroom.err.log` na raiz do projeto. O PID retornado por `-PassThru` é gravado em `boardroom.pid`.
- `stop.bat` lê o PID de `boardroom.pid` e executa `taskkill /PID <pid> /T /F` (mata o processo e a árvore de filhos), depois remove `boardroom.pid`.

## Detecção de estado (pid file "sujo")

Antes de iniciar, `start.bat` verifica se `boardroom.pid` já existe:
- Se existir, checa via `tasklist /FI "PID eq <pid>"` se aquele processo realmente está rodando.
  - Se estiver rodando: avisa "Boardroom já está em execução (PID <pid>)" e sai sem iniciar um segundo processo.
  - Se não estiver rodando (pid file órfão de uma queda anterior, ex.: processo morto manualmente ou crash): remove o `boardroom.pid` antigo silenciosamente e prossegue com o start normal.

`stop.bat` não precisa dessa checagem: se `boardroom.pid` não existir, avisa "Boardroom não parece estar rodando" e sai sem erro. Se existir mas o processo já estiver morto, `taskkill` falha silenciosamente (saída suprimida) e o `boardroom.pid` é removido do mesmo jeito — `stop.bat` sempre termina em estado limpo (sem pid file), independente do processo estar vivo ou não.

## Pré-condições e erros

- `start.bat` verifica se `.venv\Scripts\python.exe` existe antes de tentar iniciar. Se não existir, imprime uma mensagem clara apontando para as instruções de setup do `README.md` (`python -m venv .venv` + `pip install -r requirements.txt`) e sai sem tentar rodar nada.
- Nenhuma verificação de porta ocupada por outro processo (fora do controle do Boardroom) é feita — se a porta 8000 já estiver em uso por outra coisa, o erro aparecerá em `boardroom.err.log` e o usuário vai precisar investigar manualmente. Isso é aceitável pro escopo local de um único usuário.

## Arquivos gerados em runtime

`boardroom.pid`, `boardroom.log`, `boardroom.err.log` são criados na raiz do projeto e adicionados ao `.gitignore`.

## Fluxo esperado

1. Usuário roda (duplo-clique ou terminal) `start.bat`.
2. Servidor sobe em background, PID salvo, navegador abre em `http://localhost:8000` depois de um `timeout /t 2` (dá tempo do uvicorn subir antes de abrir a aba).
3. Usuário usa o app normalmente, terminal (se usado) fica livre para outros comandos.
4. Quando terminar, roda `stop.bat`, que mata o processo e limpa o `boardroom.pid`.

## Testes (verificação manual)

Não há framework de teste automatizado para scripts `.bat` neste projeto — a verificação é manual:
1. Rodar `start.bat` com `.venv` presente → servidor responde em `http://localhost:8000`, `boardroom.pid` existe e contém um PID válido, navegador abre.
2. Rodar `start.bat` de novo enquanto o primeiro ainda está no ar → deve recusar com aviso, sem duplicar o processo.
3. Rodar `stop.bat` → processo morre (confirmar via `tasklist`), porta 8000 fica livre, `boardroom.pid` é removido.
4. Rodar `stop.bat` sem nada rodando → avisa que não há servidor ativo, não trava, não lança erro.
5. Simular pid file órfão (criar `boardroom.pid` manualmente com um PID inexistente) e rodar `start.bat` → detecta que o processo não existe, remove o pid file antigo, inicia normalmente.
6. Rodar `start.bat` sem `.venv` criado → mensagem clara apontando para o setup do README, sem stack trace do Python.

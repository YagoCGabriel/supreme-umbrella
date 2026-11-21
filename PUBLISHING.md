# Publicação no GitHub

O projeto já contém um commit Git local. Para criar o repositório remoto e enviar a branch `main`:

1. Instale Git e GitHub CLI: https://cli.github.com/
2. No terminal, autentique: `gh auth login`
3. Abra esta pasta no PowerShell.
4. Execute `./publish.ps1`.
5. Escolha a visibilidade solicitada pelo GitHub CLI.

Também é possível definir a visibilidade explicitamente:

- `./publish.ps1 -Visibility public`
- `./publish.ps1 -Visibility private`

O script cria `cifraclub-api-v2` na conta autenticada e publica a branch `main`. Ele interrompe a execução se já existir um `origin`, para não sobrescrever uma configuração Git existente.

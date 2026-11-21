param(
    [ValidateSet('prompt', 'public', 'private')]
    [string]$Visibility = 'prompt',
    [string]$RepositoryName = 'cifraclub-api-v2'
)

$ErrorActionPreference = 'Stop'

if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    throw 'GitHub CLI (gh) não encontrado. Instale em https://cli.github.com/ e execute gh auth login.'
}

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw 'Git não encontrado. Instale Git for Windows e tente novamente.'
}

gh auth status | Out-Host
$login = (gh api user --jq .login).Trim()
if (-not $login) {
    throw 'Não foi possível identificar a conta autenticada no GitHub.'
}

if (-not (Test-Path (Join-Path (Get-Location) '.git'))) {
    throw 'Execute este script na pasta raiz do projeto, que deve conter a pasta .git.'
}

$existingRemote = git remote get-url origin 2>$null
if ($LASTEXITCODE -eq 0 -and $existingRemote) {
    throw "Já existe um remote origin ($existingRemote). Verifique-o antes de publicar."
}

$args = @('repo', 'create', $RepositoryName, '--source', '.', '--remote', 'origin', '--push')
if ($Visibility -eq 'public') { $args += '--public' }
if ($Visibility -eq 'private') { $args += '--private' }

Write-Host "Conta autenticada: $login"
Write-Host "Repositório: $login/$RepositoryName"
if ($Visibility -eq 'prompt') {
    Write-Host 'O GitHub CLI solicitará a visibilidade do repositório.'
}

gh @args
if ($LASTEXITCODE -ne 0) {
    throw 'A criação/publicação falhou. Veja a mensagem do GitHub CLI acima.'
}

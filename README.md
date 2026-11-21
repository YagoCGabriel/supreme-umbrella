# Cifra Club API — versão 2

API Flask para transformar HTML de páginas musicais em dados estruturados. O projeto mantém o endpoint legado do repositório original e adiciona uma API versionada, parsing de metadados, acordes, seções e blocos de tablatura.

> **Importante sobre a fonte:** os termos publicados pelo Cifra Club restringem métodos automatizados de prospecção e extração de dados. Por isso, a coleta remota está **desativada por padrão**. A aplicação pode analisar HTML fornecido pelo próprio consumidor (`POST /v1/parse/html`), mas a busca remota só deve ser ativada quando houver autorização aplicável do provedor. Este projeto não contorna bloqueios, autenticação, CAPTCHA ou limites da origem.

## O que mudou

- Parsing desacoplado do transporte HTTP.
- Extração em múltiplas estratégias: metadados JSON-LD, Open Graph, seletores semânticos e os elementos `.cifra`, `.cifra_cnt` e `pre` usados pela estrutura antiga.
- Preservação de espaços e linhas vazias dentro de `<pre>` para não destruir o alinhamento das cifras e tablaturas.
- Normalização de acordes, seções e blocos de tablatura.
- Validação do resultado: respostas incompletas são marcadas como `partial`, com avisos, e não como sucesso.
- Timeout, tentativas limitadas, espera entre chamadas, limite de tamanho de resposta e fallback Selenium opcional.
- Cache TTL em memória por processo.
- Erros HTTP diferenciados e endpoint de health check.
- Testes unitários e de API com HTML fixture sintético.
- Endpoint legado mantido: `/artists/<artist>/songs/<song>`.

## Deploy em plataformas Flask/WSGI

A aplicação expõe um entrypoint convencional na raiz:

- Módulo: `wsgi.py`
- Objeto Flask: `app`
- Referência WSGI: `wsgi:app`

O arquivo `wsgi.py` importa a instância criada em `api.py`. Isso permite que plataformas que procuram automaticamente `wsgi.py` encontrem a aplicação sem depender da descoberta de `api.py`.

Configuração recomendada na plataforma:

- **Root / working directory:** raiz do repositório
- **Build command:** `pip install -r requirements.txt`
- **Start command:** `gunicorn --workers 2 --threads 4 --timeout 30 --bind 0.0.0.0:$PORT wsgi:app`

O `Procfile` já declara esse processo. Se a plataforma permitir configurar manualmente o entrypoint Flask, use `wsgi:app`. Não use `app:app`: há também um diretório de pacote chamado `app/`, então esse nome pode resolver para o pacote em vez do módulo WSGI.

## Requisitos

- Python 3.12+ ou Docker com Docker Compose v2.
- Acesso à rede somente para uso remoto autorizado; o parsing local de HTML não precisa de acesso externo.

## Executar localmente

```bash
cd supreme-umbrella
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r app/requirements.txt
python app/api.py
```

A API ficará disponível em `http://localhost:3000`.

## Executar com Docker

```bash
docker compose build
docker compose up
```

O serviço Selenium é opcional e fica no profile `browser`:

```bash
docker compose --profile browser up --build
```

Apenas subir o contêiner Selenium não ativa a coleta remota. As duas variáveis de autorização precisam estar configuradas explicitamente, e seu uso pressupõe autorização aplicável. O contêiner instala `requirements-prod.txt`, que acrescenta Gunicorn ao conjunto principal.

## Configuração

Copie `.env.example` para `.env` e ajuste apenas o necessário. O Docker Compose não carrega `.env` automaticamente para o contêiner sem `env_file` ou interpolação; para execução local, exporte as variáveis no shell ou use um gerenciador de ambiente.

| Variável | Padrão | Descrição |
|---|---:|---|
| `PORT` | `3000` | Porta HTTP da aplicação |
| `CACHE_TTL_SECONDS` | `300` | Tempo de cache em segundos; `0` desativa |
| `CIFRACLUB_FETCH_ENABLED` | `false` | Habilita a tentativa de coleta remota |
| `CIFRACLUB_FETCH_AUTHORIZED` | `false` | Confirma que o operador possui autorização aplicável |
| `SCRAPER_TIMEOUT_SECONDS` | `15` | Timeout por chamada |
| `SCRAPER_MAX_RETRIES` | `2` | Retentativas para falhas transitórias |
| `SCRAPER_MIN_INTERVAL` | `1.5` | Intervalo mínimo entre requisições no processo |
| `SCRAPER_BROWSER_FALLBACK` | `false` | Permite fallback Selenium quando configurado |
| `SELENIUM_REMOTE_URL` | vazio | URL do Selenium Grid remoto |

A coleta remota somente é ativada quando **as duas** variáveis `CIFRACLUB_FETCH_ENABLED` e `CIFRACLUB_FETCH_AUTHORIZED` são `true`. Essas flags são uma proteção operacional, não uma autorização jurídica.

## Endpoints

### Utilidade e diagnóstico

| Método | Endpoint | Descrição |
|---|---|---|
| `GET` | `/` | Informações básicas da API |
| `GET` | `/health` | Alias de health check |
| `GET` | `/v1/health` | Estado dos componentes e da coleta remota |
| `GET` | `/v1/docs` | Índice de endpoints |

### Parsing e músicas

| Método | Endpoint | Descrição |
|---|---|---|
| `POST` | `/v1/parse/html` | Analisa HTML fornecido no corpo da requisição, sem acessar a internet |
| `GET` | `/v1/songs/<artist>/<song>` | Retorna a música estruturada (coleta remota sujeita às flags de autorização) |
| `GET` | `/v1/songs/<artist>/<song>/metadata` | Metadados e configurações musicais identificadas |
| `GET` | `/v1/songs/<artist>/<song>/chords` | Acordes reconhecidos no conteúdo |
| `GET` | `/v1/songs/<artist>/<song>/sections` | Seções e linhas agrupadas |
| `GET` | `/v1/songs/<artist>/<song>/tabs` | Blocos de tablatura reconhecidos |
| `GET` | `/artists/<artist>/songs/<song>` | Endpoint legado compatível |

O projeto não implementa busca global de catálogo nem inventa diagramas de acordes, tonalidade, BPM ou afinação quando esses dados não aparecem na origem. Campos não encontrados ficam como `null`, `[]` ou ausentes conforme o caso.

### Exemplo: parsing de HTML fornecido

```bash
curl -X POST http://localhost:3000/v1/parse/html \
  -H 'Content-Type: application/json' \
  --data-binary @payload.json
```

O `payload.json` deve ter o seguinte formato:

```json
{
  "source_url": "https://www.cifraclub.com.br/artista/musica/",
  "html": "<html>...HTML completo...</html>"
}
```

Limite de entrada: 2 MiB. O endpoint apenas processa o texto enviado pelo cliente; não visita `source_url`.

### Exemplo de resposta estruturada

```json
{
  "artist": "Artista Exemplo",
  "name": "Canção de Teste",
  "youtube_url": null,
  "cifraclub_url": null,
  "composers": [],
  "music": {
    "key": null,
    "tuning": null,
    "capo": null,
    "tempo": null,
    "time_signature": null
  },
  "chords": ["Am", "G"],
  "sections": [
    {"name": "Intro", "type": "intro", "start_line": 1, "lines": ["Am  G"]}
  ],
  "tabs": [],
  "cifra": ["[Intro] Am  G"],
  "extraction": {
    "status": "success",
    "method": "html_parser",
    "confidence": 0.98,
    "warnings": []
  }
}
```

O exemplo é ilustrativo. A pontuação de confiança é uma heurística de integridade dos campos extraídos, não uma probabilidade estatística de correção musical.

## Estratégia do parser

1. Busca título e artista pelos seletores conhecidos e metadados Open Graph.
2. Analisa blocos JSON-LD quando presentes.
3. Procura conteúdo preservando o `<pre>` e os espaços originais.
4. Reconhece acordes em linhas que parecem linhas de cifra, evitando interpretar palavras comuns como acordes.
5. Agrupa cabeçalhos entre colchetes como seções e linhas de tablatura consecutivas como blocos.
6. Retorna avisos quando informações fundamentais não são encontradas.

O HTML dos sites pode mudar. Os seletores são estratégias de extração, não um contrato estável da plataforma; por isso os testes de regressão devem receber fixtures reais somente quando seu uso e armazenamento forem autorizados.

## Fallback de navegador

O parser HTTP é a primeira opção. O Selenium remoto é fallback opcional, iniciado apenas se:

- a coleta remota tiver sido explicitamente habilitada e autorizada nas duas flags;
- `SCRAPER_BROWSER_FALLBACK=true`;
- `SELENIUM_REMOTE_URL` estiver definido;
- a dependência Selenium estiver instalada e o Grid estiver acessível.

O driver é sempre encerrado em `finally`. O fallback não tenta contornar bloqueios ou controles de acesso.

## Testes

```bash
pip install -r app/requirements-dev.txt
pytest
```

Os testes usam HTML sintético local. Eles não acessam o Cifra Club e não dependem de Selenium ou Redis.

## Limitações conhecidas

- O parser não garante a extração de diagramas gráficos de acordes. Isso depende de atributos/estruturas disponíveis no HTML da página e ainda exige fixtures específicos.
- BPM, tonalidade, compasso, capotraste e afinação só são preenchidos quando estão explicitamente disponíveis em metadados reconhecidos.
- A separação de seções usa cabeçalhos textuais entre colchetes; páginas com estrutura diferente podem exigir um parser específico.
- O cache é local ao processo e não é compartilhado entre workers/instâncias. Para múltiplas réplicas, substitua por Redis.
- O endpoint de busca de músicas não foi incluído: implementar busca sobre catálogo remoto exigiria acesso automatizado adicional e autorização própria.

## Uso responsável

Consulte os [termos de uso do Cifra Club](https://www.cifraclub.com.br/aviso-legal.html) antes de integrar qualquer coleta. Respeite autorizações, direitos autorais, privacidade, limites e políticas do provedor. O operador da API é responsável pelo uso e pela distribuição dos dados.

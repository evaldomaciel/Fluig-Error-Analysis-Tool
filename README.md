# Fluig Error Analysis Tool

Ferramenta de linha de comando para analisar o `server.log` do **TOTVS Fluig** (WildFly): agrupa os erros,
separa ruído de problema real, investiga a origem dos erros mais frequentes e identifica causas de lentidão.
Gera relatório no terminal, em texto, CSV, JSON e **PDF**.

Roda em Windows e Linux, lê o arquivo em streaming (logs de centenas de MB sem carregar tudo na memória) e
não precisa de nenhum serviço externo.

## Requisitos

- Python 3.8 ou superior
- `reportlab`, apenas para gerar PDF

## Instalação

```bash
# opção 1: instalar como comando (fluig-log-analyzer)
pip install ".[pdf]"

# opção 2: sem instalar, só a dependência do PDF
pip install -r requirements.txt
python -m fluig_log_analyzer --help
```

No Linux, prefira um ambiente virtual: `python3 -m venv .venv && . .venv/bin/activate`.

## Uso

```bash
# relatório no terminal
fluig-log-analyzer server.log

# relatório em PDF
fluig-log-analyzer server.log --pdf relatorio.pdf

# tudo (PDF, TXT, CSV e JSON) numa pasta, sem imprimir no terminal
fluig-log-analyzer server.log --tudo saida/ -q

# vários arquivos, inclusive rotacionados e .gz
fluig-log-analyzer "/opt/fluig/appserver/domain/servers/*/log/server.log*" --tudo saida/
```

Sem instalar, troque `fluig-log-analyzer` por `python -m fluig_log_analyzer` (dentro da pasta do projeto).

### Opções

| Opção | Padrão | Descrição |
|---|---|---|
| `--pdf ARQUIVO` | — | Gera o relatório em PDF |
| `--txt ARQUIVO` | — | Grava o relatório em texto |
| `--csv PASTA` | — | Grava as tabelas em CSV (separador `;`, abre direto no Excel) |
| `--json ARQUIVO` | — | Grava os dados agregados em JSON |
| `--tudo PASTA` | — | Gera PDF, TXT, CSV e JSON na pasta |
| `-q`, `--silencioso` | — | Não imprime o relatório no terminal |
| `--niveis` | `ERROR,FATAL,SEVERE` | Níveis tratados como erro (ex.: `ERROR,WARN`) |
| `--top` | `25` | Itens por lista |
| `--encoding` | `utf-8` | Codificação do log (ex.: `latin-1`) |
| `--horario-comercial` | `8-18` | Faixa usada para procurar intervalos sem log |
| `--intervalo-sem-log` | `5` | Segundos sem nenhum log para reportar um intervalo |
| `--dataset-lento` | `10` | Média (s) a partir da qual um dataset é considerado lento |
| `--sync-longo` | `600` | Duração (s) de sincronização de dataset considerada longa |
| `--exportacao-grande` | `300` | Duração (s) de exportação da pesquisa de solicitações considerada grande |
| `--investigar` | `3` | Quantas assinaturas de erro investigar em detalhe |
| `--mostrar-usuarios` | desligado | Exibe logins nos relatórios (por padrão são anonimizados) |
| `--autor` | — | Autor gravado nos metadados do PDF |

## O que a análise faz

**Erros**
- Junta cada linha `ERROR` com sua stack trace, inclusive as impressas em `[stderr]`, onde cada linha da
  stack tem timestamp próprio (por isso contar linhas com `" ERROR "` superestima os erros).
- Normaliza as mensagens (números, UUIDs, usuários) para agrupar ocorrências iguais em *assinaturas*.
- Une erros de loggers diferentes na mesma thread e no mesmo segundo em um único *incidente*.
- Classifica por tema: SSL, SMTP, banco/transação, memória, bug de script, validação de negócio,
  permissão, arquivo/volume, ruído de conexão encerrada etc.
- Separa erros de customização por processo, evento e mensagem (`validateForm`, `displayFields`...).
- **Investiga** as assinaturas mais frequentes (excluindo ruído): cadeia de chamadas do produto na stack,
  presença ou não de código customizado, o que a thread fazia antes do erro, dispersão por usuários e
  solicitações.

**Lentidão**
- Procura sinais de saturação: pool de conexões esgotado, timeout de transação, OutOfMemory/GC, threads
  travadas, timeout de rede, deadlock.
- Lê os tempos medidos pelo próprio Fluig: `JSChronos` (scripts e datasets lentos), `datasetSync` e
  `exportProcessSearcher` (exportações da pesquisa de solicitações).
- Associa avisos de `createDataset`/`datasetSync` ao dataset executado pela mesma thread.
- Mede intervalos sem log no horário comercial, concorrência de requisições e volume de log por logger.

**Achados**: regras transformam os números em uma lista de achados com severidade (Alta, Média, Baixa,
Info), evidência, recomendação e frente responsável (Customização, Infraestrutura, TOTVS, Usuários).

## Saídas

| Arquivo | Conteúdo |
|---|---|
| `relatorio_<log>.pdf` | Resumo executivo, método, lentidão, erros, investigação, distribuição por hora e recomendações |
| `relatorio_<log>.txt` | O mesmo conteúdo do terminal |
| `dados_<log>.json` | Dados agregados para integração |
| `csv/achados.csv` | Achados com severidade e recomendação |
| `csv/erros_assinaturas.csv` | Todas as assinaturas de erro, com tema e causa raiz |
| `csv/erros_por_tema.csv` | Incidentes por tema |
| `csv/erros_customizacao.csv` | Erros de script por processo e evento |
| `csv/lentidao_jschronos.csv` | Operações lentas registradas pelo Fluig |
| `csv/lentidao_por_dataset.csv` | `createDataset`/`datasetSync` lentos por dataset |
| `csv/datasets_execucoes.csv` | Datasets mais executados |
| `csv/exportacoes.csv` | Exportações da pesquisa de solicitações |
| `csv/volume_por_logger.csv` | Linhas de log por logger |

## Privacidade

Logins de usuários são anonimizados por padrão em todas as saídas. Use `--mostrar-usuarios` apenas para
uso interno. Logs e relatórios gerados ficam fora do git (`.gitignore`).

## Limitações

- O log não traz métricas de CPU, memória, GC ou banco: confirme as conclusões de lentidão no
  monitoramento do servidor e do banco.
- A associação entre avisos de `createDataset`/`datasetSync` e o nome do dataset é aproximada (último
  dataset executado pela mesma thread). Os itens `DatasetFactory.getDataset.<nome>` trazem o nome exato.
- O formato esperado é o padrão do Fluig/WildFly:
  `AAAA-MM-DD HH:MM:SS,mmm NIVEL [logger] (thread) mensagem`.

## Desenvolvimento

```bash
python -m unittest -v
```

Estrutura:

| Módulo | Responsabilidade |
|---|---|
| `parser.py` | Leitura em streaming (inclui `.gz`), agrupa linhas de continuação |
| `errors.py` | Eventos, assinaturas, incidentes, temas, customização e investigação |
| `performance.py` | Saturação, JSChronos, datasets, exportações, intervalos, volume |
| `findings.py` | Regras que geram os achados e recomendações |
| `report_text.py` / `report_pdf.py` / `exporters.py` | Saídas em texto, PDF, CSV e JSON |
| `cli.py` | Linha de comando |

Para novos temas de erro, edite `THEME_RULES` em `errors.py`; para novos sinais de lentidão,
`SATURATION_SIGNALS`/`OTHER_SIGNALS` em `performance.py`; para novas recomendações, `findings.py`.

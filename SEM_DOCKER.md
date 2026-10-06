# Área remota: SQLite, painel e IpeaIA sem Docker

O pacote `transferencia/bpc-jud.sqlite.gz` contém todas as tabelas e dados da
aplicação exportados do PostgreSQL local. A cópia de 05/10/2026 tem 2.584
processos e 125.692 movimentos. Não é o SQLite legado do coletor antigo.

## Aqui: enviar o código e o pacote pelo Git

Inclua no commit as alterações de `ingestion/`, a documentação, `.env.example`
e `transferencia/bpc-jud.sqlite.gz`, e faça push. O arquivo `.env` permanece
local. O pacote já foi gerado; não é necessário exportá-lo outra vez.

```powershell
git add ingestion docs .env.example README.md RESTAURAR_BANCO.md PROMPT_REVISAO_IPEAIA.md SEM_DOCKER.md transferencia/bpc-jud.sqlite.gz
git commit -m "Adiciona base SQLite e execucao remota sem Docker"
git push origin main
```

## Lá: instalar e abrir a base

Requisito: Python 3.11 ou superior. Execute na raiz do clone remoto:

```powershell
git pull
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install ./ingestion
.\.venv\Scripts\python.exe -m bpc_ingestion.portable inicializar
```

Se `py` não existir, use `python` no comando de criação do ambiente. O último
comando descompacta a base em `data/bpc-remote.sqlite` e mostra o total de
processos. Ele preserva um arquivo já existente e recusa sobrescrevê-lo.
Não inicialize novamente uma base que já recebeu resultados da IA.

Se ainda não existir `.env`, copie `.env.example` para `.env`. Edite o `.env`
existente ou recém-criado e configure:

```dotenv
DATABASE_URL=sqlite:///./data/bpc-remote.sqlite
IPEAIA_API_TOKEN=SEU_TOKEN_LOCAL
IPEAIA_BASE_URL=https://ipeagpt.ipea.gov.br/api/v1
IPEAIA_MODEL=glm-5.1
IPEAIA_TIMEOUT_SECONDS=600
```

O painel e os comandos leem o `.env` automaticamente. Execute sempre da raiz
do projeto para usar o mesmo arquivo SQLite. Não é necessário instalar
PostgreSQL, Docker ou pgvector no servidor.

## Abrir o painel

```powershell
.\.venv\Scripts\python.exe -m uvicorn bpc_ingestion.api:app --host 127.0.0.1 --port 8000
```

Abra `http://localhost:8000/admin/processos` no navegador da área remota.
O terminal fica ocupado pela API; abra um segundo terminal para os comandos:

```powershell
.\.venv\Scripts\python.exe -m bpc_ingestion resumo
.\.venv\Scripts\python.exe -m bpc_ingestion ipeaia-modelos
.\.venv\Scripts\python.exe -m bpc_ingestion ipeaia-triagem --limit 1
.\.venv\Scripts\python.exe -m bpc_ingestion ipeaia-triagem --limit 1 --executar
```

A saída da IA fica em `extracoes_ia` dentro de `data/bpc-remote.sqlite`, com
status pendente de revisão, e pode ser vista no detalhe do processo no painel.
Sem `--executar`, há somente prévia e nenhuma chamada à IpeaIA.

Para modelos lentos, o tempo limite de rede é 600 segundos por padrão e pode
ser alterado por `IPEAIA_TIMEOUT_SECONDS` ou `--timeout 900`. Um timeout mostra
mensagem específica e não repete automaticamente a geração. Erros de rede
mostram a causa (por exemplo, certificado ou DNS). Atualize o código remoto
com `git pull` e `python -m pip install ./ingestion` antes de usar a nova opção.

## Diagnóstico de respostas rejeitadas

Se a resposta for rejeitada, o comando mostra o campo e o índice da evidência
inválida e salva um diagnóstico em `data/ipeaia_rejeitadas/<id>.json`. O arquivo
contém a entrada enviada, modelo, versão do prompt, erro e resposta da API,
sem token. Ele fica fora do Git. A resposta inválida não entra em `extracoes_ia`;
o processo permanece pendente para nova tentativa. Não há repetição automática.
Este diagnóstico de formato não substitui a revisão humana dos fatos citados.

JSON dentro de um único bloco Markdown também é aceito. Caminhos de campos
existentes são normalizados sem alterar o contrato. Se aparecer `Modelo
divergente`, confirme o ID com `ipeaia-modelos` e use `--model` com o modelo
correto; a aplicação não grava uma resposta atribuída a outro modelo.

O prompt atual é `bpc_triagem_api_v1.1` e orienta uma evidência por campo,
incluindo `tribunal` e `grau`. Se o modelo ainda combinar campos com `/`, o
parser separa a evidência somente quando todos os componentes existem na
entrada. Resultados v1.0 permanecem no banco; a nova versão pode triar novamente
os mesmos processos. Não é necessário restaurar ou inicializar a base de novo.

## Processar um lote com resumo em linguagem simples

Atualize a instalação e execute um lote inicial de 10 processos:

```powershell
git pull
.\.venv\Scripts\python.exe -m pip install --upgrade ./ingestion
.\.venv\Scripts\python.exe -m bpc_ingestion ipeaia-triagem --limit 10 --executar --timeout 1200
```

O log deve mostrar `bpc_triagem_api_v1.2`. Essa versão envia a hierarquia TPU
do banco e grava `resumo_caso` em linguagem simples junto das categorias e
evidências em `extracoes_ia`. No painel, o resumo aparece antes do JSON completo.
Se o catálogo TPU não estiver na cópia, a hierarquia fica vazia; não se inventam
ancestrais. Não restaure a base de novo para atualizar o código.

As chamadas são sequenciais. Pela duração observada de cerca de 6 minutos por
processo, 10 podem levar aproximadamente uma hora; isso varia com fila e modelo.
Pode usar até `--limit 50`. Repetir o comando seleciona pendentes do mesmo
modelo e versão; versões anteriores são preservadas e podem ser retriadas.
Cada sucesso é salvo imediatamente; erro interrompe o lote, mas mantém sucessos
anteriores. Não repetir em loop um processo rejeitado sem avaliar o diagnóstico.

## Coletar fontes públicas complementares (sem credenciais)

Na raiz do clone remoto, usando o `.env` que aponta para o SQLite existente:

```powershell
git pull
.\.venv\Scripts\python.exe -m pip install --upgrade ./ingestion
.\.venv\Scripts\python.exe -m bpc_ingestion documentos-publicos --fonte stj --recursos 2 --limit 50
.\.venv\Scripts\python.exe -m bpc_ingestion documentos-publicos --fonte cjf --base TRF1 --query LOAS --paginas 2 --limit 50
.\.venv\Scripts\python.exe -m bpc_ingestion documentos-publicos --fonte cjf --base JEF1 --query LOAS --paginas 2 --limit 50
.\.venv\Scripts\python.exe -m bpc_ingestion inss-indeferimentos --competencia 202608 --uf DF
.\.venv\Scripts\python.exe -m bpc_ingestion arquivo-trf1 --numero 1053078-37.2022.4.01.3400
```

Os comandos criam somente as novas tabelas aditivas; não restauram nem apagam
processos ou extrações. No PostgreSQL, aplicar a migração Alembic 0006 no fluxo
normal do projeto (o ambiente Docker local deve estar ligado). No SQLite, não
executar migrações PostgreSQL.

Abra `http://localhost:8000/admin/documentos` (reinicie o servidor após atualizar).
O corpus complementar NÃO é a população DF/RIDE: documentos sem CNJ explícito
coincidente ficam separados. A IpeaIA de metadados ainda não recebe esses textos.
Os agregados INSS não são processos e não podem fornecer um motivo individual.

O INSS lê a planilha inteira antes de gravar os agregados (um arquivo mensal
grande pode levar minutos). A Bronze fica em `data/raw/inss_indeferimentos/`.
Se precisar repetir a agregação de um arquivo já baixado, use `--arquivo`
com o caminho XLSX.gz impresso no log e a mesma competência; todos os períodos
das linhas são conferidos. Não versionar essa planilha: contém microdados.

Limites: STJ até 24 recursos JSON por conjunto, sem ZIP histórico por enquanto;
CJF até 20 páginas e 500 documentos por execução, respeitando intervalo entre
requisições. Para mudar a cobertura, selecione termos/períodos sem supor que isso
representa todos os processos. `arquivo-trf1` só consulta disponibilidade;
`existeProcesso:false` não significa inexistência no DataJud. Anti-robô resulta
em `bloqueada`, nunca é contornado.

## Atualizar a cópia no futuro

A origem PostgreSQL permanece na máquina local. O módulo
`bpc_ingestion.portable exportar` lê essa origem e gera um novo pacote SQLite.
Escolha outro caminho de saída para conservar o pacote anterior. A exportação
valida todas as tabelas antes da transferência. Novas classificações feitas no
SQLite remoto ficam no arquivo remoto e não retornam automaticamente à origem.
Nesta etapa, o SQLite armazena embeddings como JSON; a busca vetorial exige
PostgreSQL. Não use Alembic nesta cópia portátil.

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

## Documentos publicados TNU/TRF3/TRF5

Para buscar vários documentos da TNU sem fornecer links individuais:

```powershell
git pull
.\.venv\Scripts\python.exe -m pip install --upgrade ./ingestion
.\.venv\Scripts\python.exe -m bpc_ingestion tnu-pesquisa --query LOAS --limit 10 --paginas 5 --timeout 120
```

Repetir pula URLs já concluídas na mesma versão do parser e consulta as
próximas pendentes dentro das cinco páginas (10 resultados por página).
Quando não houver pendentes nessas páginas, aumente `--paginas`; suporta
até 100 páginas e 50 URLs por lote. `--retentar` reconsulta também sucessos
anteriores. O limite conta URLs: uma página de inteiro teor pode trazer
vários artigos do mesmo processo. Não é amostra representativa nem garantia
de cobertura DF/RIDE. Busca e documentos ficam no banco/Bronze locais,
sem token e sem envio automático à IA. Cada URL bem-sucedida é salva antes
da próxima; erro interrompe o lote sem apagar os documentos anteriores.

Com a instalação atualizada, pode baixar links oficiais conhecidos, sem token:

```powershell
.\.venv\Scripts\python.exe -m bpc_ingestion documentos-publicados --urls 'https://eproctnu-jur.cjf.jus.br/eproc/externo_controlador.php?acao=jurisprudencia%40jurisprudencia%2Fdownload_inteiro_teor&id_jurisprudencia=771782816310867611465715976907' 'https://jurisprudencia.trf5.jus.br/jurisprudencia/exibir.wsp?tmp.id_documento=165824' --timeout 120
```

Esse piloto foi testado na base local: seis documentos TNU (um processo) e um
TRF5; repetir não duplicou versões. Guarda em `documentos_publicos`, com origem
e Bronze local, e aparece em `/admin/documentos`. Não altera a população
DataJud, não envia textos à IpeaIA e não anonimiza dados pessoais. Os arquivos
novos não acompanham o pacote original do Git; execute a coleta no ambiente
remoto, sem restaurar o banco por cima dos resultados existentes.

Aceita até 50 URLs oficiais explícitas por execução, não IDs inventados nem
links de autos autenticados. A rota TRF3 também é suportada; o exemplo
`https://web.trf3.jus.br/acordaos/Acordao/BuscarDocumentoPje/344467700`
teve timeout na chamada direta local, então disponibilidade nesse ambiente
ainda não está comprovada. Falha interrompe o lote e fica registrada em
`coletas`, preservando sucessos anteriores. Não contorna CAPTCHA/anti-robô.

## Coletar fontes públicas complementares (sem credenciais)

Na raiz do clone remoto, usando o `.env` que aponta para o SQLite existente:

```powershell
git pull
.\.venv\Scripts\python.exe -m pip install --upgrade ./ingestion
.\.venv\Scripts\python.exe -m bpc_ingestion documentos-publicos --fonte stj --recursos 2 --limit 50
.\.venv\Scripts\python.exe -m bpc_ingestion documentos-publicos --fonte cjf --base TRF1 --query LOAS --paginas 2 --limit 50
.\.venv\Scripts\python.exe -m bpc_ingestion documentos-publicos --fonte cjf --base JEF1 --query LOAS --paginas 2 --limit 50
.\.venv\Scripts\python.exe -m bpc_ingestion inss-indeferimentos --competencia 202608 --uf DF
.\.venv\Scripts\python.exe -m bpc_ingestion inss-concessoes --competencia 202608 --uf DF
.\.venv\Scripts\python.exe -m bpc_ingestion arquivo-trf1 --numero 1053078-37.2022.4.01.3400
```

Para procurar documentos dos próprios processos da amostra, execute:

```powershell
.\.venv\Scripts\python.exe -m bpc_ingestion cjf-amostra --base JEF1 --limit 10 --timeout 120
.\.venv\Scripts\python.exe -m bpc_ingestion cjf-amostra --base TRF1 --limit 10 --timeout 120
```

Repetir avança para os próximos candidatos públicos BPC de órgãos de Brasília;
não depende de ter passado pela IpeaIA. As duas bases têm controles separados.
Vazios e sucessos ficam registrados em `coletas`; não comprovam inexistência
de decisão nem concessão inicial. Para rever consultas concluídas, use
`--retentar`; para consultar mais páginas por processo, use `--paginas` (1..20).
Erros interrompem o lote e mantêm gravações anteriores. Busca sem credenciais,
sem contornar CAPTCHA e sem enviar o texto à IA. CNJ mencionado apenas no
corpo do documento não gera vínculo: o número estruturado tem que coincidir.

Para incluir os acórdãos históricos do STJ, opcionalmente:

```powershell
.\.venv\Scripts\python.exe -m bpc_ingestion documentos-publicos --fonte stj --conjuntos espelhos-de-acordaos-segunda-turma --historico --recursos 2 --limit 100 --timeout 180
```

O histórico vem primeiro. O ZIP da Segunda Turma tem cerca de 95 MB de download
e 414 MB expandidos; o leitor não extrai arquivos no disco. `--limit` limita
documentos temáticos observados, não significa que o histórico inteiro foi
importado. Repetir o mesmo comando deduplica versões, mas não avança um
checkpoint histórico: aumente o limite para ampliar essa coleta. Os documentos
continuam separados da amostra e não entram automaticamente na IpeaIA.

Os comandos criam somente as novas tabelas aditivas; não restauram nem apagam
processos ou extrações. No PostgreSQL, aplicar a migração Alembic 0006 no fluxo
normal do projeto, incluindo 0007 para concessões (o ambiente Docker local deve estar ligado). No SQLite, não
executar migrações PostgreSQL.

Abra `http://localhost:8000/admin/documentos` (reinicie o servidor após atualizar).
O corpus complementar NÃO é a população DF/RIDE: documentos sem CNJ explícito
coincidente ficam separados. A IpeaIA de metadados ainda não recebe esses textos.
Os agregados INSS não são processos e não podem fornecer um motivo individual.

As concessões aparecem em seção separada no mesmo painel. O XLSX agosto/2026
tem cerca de 126 MB; a leitura inteira pode levar alguns minutos. Os campos
de despacho são códigos/descrições originais, sem classificação automática de
concessão judicial e sem ligação com processos. Não divida concessões por
indeferimentos para chamar o resultado de taxa de procedência.

O INSS lê a planilha inteira antes de gravar os agregados (um arquivo mensal
grande pode levar minutos). A Bronze fica em `data/raw/inss_indeferimentos/`.
Se precisar repetir a agregação de um arquivo já baixado, use `--arquivo`
com o caminho XLSX.gz impresso no log e a mesma competência; todos os períodos
das linhas são conferidos. Não versionar essa planilha: contém microdados.

Limites: STJ até 24 recursos JSON recentes por conjunto, com ZIP histórico opcional;
CJF até 20 páginas e 500 documentos por execução, respeitando intervalo entre
requisições. Para mudar a cobertura, selecione termos/períodos sem supor que isso
representa todos os processos. Sem `--baixar`, `arquivo-trf1` só consulta disponibilidade.
Para baixar arquivos publicados quando o processo for localizado:

```powershell
.\.venv\Scripts\python.exe -m bpc_ingestion arquivo-trf1 --numero 0003970-58.2006.4.01.4001 --baixar --limite-documentos 10
```

Esse número é um controle BPC do Piauí já presente na base, não do recorte DF/RIDE.
Substitua por um CNJ desejado. DOC/TIFF ficam na Bronze local; são registrados
em `documentos_publicos` e vinculados pelo CNJ confirmado pela listagem.
DOC binário/TIFF não ganham texto fictício: para DOC, a conversão é separada;
OCR de TIFF continua pendente.
Certidões observadas com extensão DOC mas conteúdo de texto simples são lidas
estritamente em UTF-8/Windows-1252. O painel mostra o estado da extração.
Não execute os arquivos no Word nem publique os textos brutos no Git.

Para extrair texto dos DOCs já baixados, sem rede nem Word:

```powershell
.\.venv\Scripts\python.exe -m bpc_ingestion converter-arquivo-trf1 --limit 10
.\.venv\Scripts\python.exe -m bpc_ingestion converter-arquivo-trf1 --limit 10 --executar
```

O primeiro comando é prévia. O segundo cria uma nova versão com texto
preliminar, preservando o original e conferindo seu hash. Repetir não converte
o mesmo arquivo/pipeline outra vez. Funciona em Python 3.12 sem Docker/Office;
o leitor `legacy-doc` é instalado com `pip install --upgrade ./ingestion`.
Erros interrompem o lote sem apagar versões anteriores. Extração não garante
inteiro teor, fidelidade de formatação ou validade da decisão. Não envia texto
à IpeaIA automaticamente. TIFF não é processado por esse comando.

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

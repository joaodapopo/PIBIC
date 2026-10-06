# Contrato de dados BPC Jud — v0.7

Este documento substitui o contrato v0.1 baseado em SQLite/MongoDB.

## Fontes

- DataJud: universo de processos e movimentos dos assuntos 6114, 11946 e 11947;
- Comunica PJe: comunicações e atos publicados consultados por número CNJ;
- TPU local: nomes e hierarquias de assuntos, classes e movimentos do CNJ.
- CKAN/INSS: catálogo em `recursos_externos` e indeferimentos BPC agregados por
  competência, UF, espécie e motivo em `indicadores_inss_indeferimentos`.
- CJF/TRF1/JEF1 e STJ: corpus complementar em `documentos_publicos`; vínculos
  explícitos com a amostra em `documento_processos`, sem aumentar a população.
- Portal da Transparência/CGU: `GET /api-de-dados/bpc-por-municipio`, indicadores
  mensais agregados de BPC por código IBGE, autenticados pelo cabeçalho
  `chave-api-dados`. O token vem do ambiente, nunca do código ou do payload.

## Identidades

- Entidade processo: `processos.numero_processo`, CNJ formatado e único;
- Hit DataJud: par `registros_datajud.datajud_index + datajud_id`;
- Comunicação: par `processo_id + hash_conteudo`;
- Hit e processo não são sinônimos: um processo pode possuir mais de um registro
  por grau, índice ou atualização.

## Camadas

### Bronze

Respostas imutáveis em `data/raw/<fonte>/<particao>/<data>/<execucao>.ndjson.gz`.
O conteúdo deve permanecer igual ao recebido.

### Silver

PostgreSQL normalizado:

- `coletas`;
- `tarefas_painel`;
- `processos`;
- `registros_datajud`;
- `assuntos` e `registro_assuntos`;
- `movimentos`;
- `consultas_pje`;
- `comunicacoes_pje`;
- `referencias_tpu`;
- `documento_chunks`;
- `extracoes_ia`.
- `recursos_externos` (catálogo de fontes, não indicadores importados).
- `indicadores_bpc_municipio` (agregados da CGU; não são processos).
- `documentos_publicos`, `documento_processos` (corpus complementar e vínculos).
- `indicadores_inss_indeferimentos` (agregados administrativos, não processos).
- `indicadores_inss_concessoes` (agregados de concessão por despacho, não processos).

O payload bruto também fica em JSONB, mas os campos usados em filtros e junções
devem possuir colunas tipadas.

### Contrato de `indicadores_bpc_municipio`

Um registro por `fonte + mes_ano + codigo_ibge + tipo_id`:

| Campo | Tipo | Significado |
| --- | --- | --- |
| `fonte` | `varchar(30)` | `portal_transparencia` |
| `mes_ano` | `integer` | Competência `AAAAMM` pedida na API |
| `data_referencia` | `date` | Data retornada pela API |
| `codigo_ibge` | `varchar(7)` | Município do indicador, não do órgão julgador |
| `municipio_nome`, `uf` | `text`, `varchar(2)` | Rótulos retornados pela API |
| `tipo_id` | `integer` | Identificador do tipo BPC retornado |
| `quantidade_beneficiados` | `bigint` | Estoque de beneficiados informado no mês |
| `valor` | `numeric(18,2)` | Valor monetário nominal informado no mês |
| `payload` | `jsonb` | Objeto original da API, sem token |
| `coleta_id`, `coletado_em` | `uuid`, `timestamptz` | Última coleta que confirmou o indicador |

As páginas completas da API, inclusive respostas vazias, ficam na Bronze sob
`data/raw/transparencia_bpc/municipios/`. Uma repetição atualiza a mesma chave
sem duplicar. O indicador não pode ser unido diretamente a uma pessoa ou ação
judicial; município do indicador e município do órgão julgador são conceitos
distintos. Não usar a quantidade como denominador de taxa de procedência.

### Gold

As views iniciais são:

- `vw_resumo_tribunais`;
- `vw_processos_analiticos`;
- `vw_cobertura_comunica`.

Novas métricas devem registrar fórmula, população, tratamento de ausências e
versão da pipeline.

## Complementação pública — contrato v0.6 (06/10/2026)

As novas fontes NÃO ampliam silenciosamente a população de processos DataJud.
STJ e jurisprudência CJF/TRF1/JEF formam corpus complementar identificado por
fonte. Um documento só é vinculado a um processo existente quando há CNJ
completo explicitamente informado em campo estruturado da origem. Não ligar por
nome, número curto do STJ, similaridade de texto ou hipótese de recurso.

### `documentos_publicos`

| Campo | Tipo lógico | Regra |
| --- | --- | --- |
| `id` | bigint PK autogerado | Identificador local |
| `fonte` | varchar(30) | `stj`, `cjf_trf1`, `trf1_arquivo` |
| `documento_id` | varchar(200) | ID estável da origem, nunca posição de página |
| `hash_conteudo` | varchar(64) | SHA-256 do JSON normalizado do documento |
| `tipo_documento`, `tribunal` | varchar(100), varchar(30) | Tipo e órgão, quando informados |
| `numero_origem` | text | Número como consta da fonte; pode não ser CNJ |
| `numeros_cnj` | JSON/JSONB lista | CNJs completos estruturados, sem extração de nomes |
| `data_publicacao`, `data_decisao` | date, nullable | Só data parseável explícita |
| `ementa`, `decisao`, `texto` | text, nullable | Conteúdo publicado; não implica autos completos |
| `url_origem` | text | Página oficial de referência |
| `recurso_url`, `arquivo_bruto` | text | Recurso oficial e caminho Bronze local |
| `payload` | JSON/JSONB objeto | Campos originais do documento, sem credenciais |
| `coleta_id` | UUID FK `coletas` | Execução que observou a versão |
| `coletado_em` | timestamp UTC | Momento da coleta |

Chave única: `fonte + documento_id + hash_conteudo`. Repetições não duplicam;
alteração gera nova versão, sem reescrever o documento antigo. Os vínculos em
`documento_processos` usam PK composta `documento_id` (FK documentos_publicos.id)
+ `processo_id` (FK processos.id), com `criterio` varchar(40) igual a
`cnj_explicito`. O vínculo indica identidade, não valida concessão inicial.

O coletor STJ pode incluir o histórico ZIP por opção explícita `--historico`.
Preserva o ZIP original antes de interpretar seus membros JSON, sem extrair
arquivos no sistema de arquivos. Aceita apenas listas de objetos, como nos
recursos JSON mensais; nomes de membros não são usados como caminhos locais.
Impõe limites de tamanho comprimido, expandido e por membro, e rejeita ZIP
criptografado ou sem JSON. O arquivo e a URL oficiais continuam sendo a origem
de todos os documentos, com a mesma chave de versão; nenhum schema muda.

Bronze de arquivos conserva bytes originais em `data/raw/<fonte>/<execucao>/`,
incluindo JSON do STJ e HTML do CJF. Não converter ementa ou dispositivo do
espelho em "sentença completa". Dados pessoais eventualmente existentes nos
textos permanecem locais e fora do Git; a triagem IpeaIA de metadados não os
envia automaticamente. Metadados e links podem ser exibidos no painel.

No PostgreSQL haverá migração aditiva; no SQLite a atualização cria somente as
novas tabelas, sem restaurar, apagar ou alterar extrações anteriores. A exportação
portátil inclui as tabelas novas e cria tabelas vazias caso ausentes na cópia
antiga; a ausência não pode ser tratada como dados coletados.

### `indicadores_inss_indeferimentos`

Agregados administrativos, não processos nem pessoas identificadas. Campos:
`id` bigint PK; `fonte` varchar(30) = `inss_indeferimentos`; `competencia` integer
AAAAMM extraído do recurso mensal solicitado; `uf` varchar(2); `especie` integer
(87 deficiência, 88 idoso); `motivo` text exatamente como publicado (espaço vazio
vira `Não informado`); `quantidade` bigint; `recurso_id` varchar(500);
`recurso_url`, `arquivo_bruto` text; `hash_arquivo` varchar(64) SHA-256 dos bytes;
`coleta_id` UUID FK coletas; `coletado_em` timestamp UTC. Chave única:
`fonte + competencia + uf + especie + motivo + hash_arquivo`. Uma nova versão
do arquivo gera outro conjunto; NÃO somar snapshots distintos da mesma
competência. O cliente grava manifesto com contagens totais/selecionadas e
filtros. Não armazena data de nascimento, sexo, DER ou outra linha individual
na Silver; os bytes da planilha ficam apenas na Bronze local ignorada pelo Git.

O importador exige cabeçalhos espécie, motivo e UF reconhecidos, filtra espécies
87/88 e UF explicitamente selecionada e só persiste após ler e agregar o
arquivo completo. Espécie é inferida apenas do código numérico da coluna, não
da descrição ou suposição. Colunas inválidas/recurso sem competência comprovada
interrompem a importação. Os motivos não são unidos a processos individuais.

O layout real de agosto/2026 contém título na primeira linha, cabeçalho na
segunda e duas colunas consecutivas `Espécie` (código e descrição). O parser
busca cabeçalho nas primeiras 20 linhas, usa a primeira coluna numérica do par
e valida os códigos linha a linha; não deduz código pela descrição. A competência
de cada linha deve corresponder ao recurso solicitado, inclusive em importação
de arquivo local. Divergência rejeita o arquivo completo antes da Silver.

CAPTCHA/anti-robô será registrado como bloqueio, não contornado e não confundido
com ausência de dados.

## IA

### `indicadores_inss_concessoes` — v0.7

Concessões administrativas agregadas, não pessoas únicas, processos ou taxa
de procedência. Fonte mensal CKAN `beneficios-concedidos-plano-de-dados-abertos-jun-2023-a-jun-2025`.
Campos: `id` bigint PK; `fonte` varchar(30) = `inss_concessoes`;
`competencia` integer AAAAMM; `uf` varchar(2); `especie` integer (87/88);
`codigo_despacho` integer; `despacho` text com descrição original ou `Não informado`;
`quantidade` bigint; `recurso_id` varchar(500); `recurso_url`, `arquivo_bruto` text;
`hash_arquivo` varchar(64); `coleta_id` UUID FK coletas;
`coletado_em` timestamp UTC. Chave única: fonte + competência + UF + espécie +
código despacho + descrição despacho + hash arquivo. Snapshots não se somam.
Mudanças nos mesmos bytes com contagem divergente rejeitam a importação.

Layout real agosto/2026: título na primeira linha, cabeçalho na segunda,
colunas consecutivas `Espécie` (código/descrição) e `Despacho` (código/descrição),
`Competência concessão` e `UF`. O importador exige essas colunas, códigos
numéricos, competência compatível em todas as linhas e agrega apenas BPC 87/88
da UF pedida. Só grava após leitura completa. Não persiste CID, nascimento,
sexo, município, escolaridade ou linhas individuais na Silver. O XLSX original
fica exclusivamente na Bronze local ignorada pelo Git.
Não classificar despacho como concessão judicial sem dicionário e validação,
nem atribuir despacho a processo. A tabela é aditiva, compatível SQLite/PG;
cópias antigas sem a tabela ganham tabela vazia, sem dados inventados.

### Execução portátil sem Docker (05/10/2026)

A base PostgreSQL pode ser exportada para SQLite mantendo as mesmas tabelas,
colunas, chaves, vínculos e dados da aplicação. O módulo `bpc_ingestion.portable`
confere contagens de todas as tabelas, integridade e chaves estrangeiras antes
de publicar `transferencia/bpc-jud.sqlite.gz`. `alembic_version` é metadado
específico do PostgreSQL e não acompanha a cópia; as três views analíticas são
recriadas com sintaxe SQLite. O antigo `SqliteStore` de `processos_raw` é legado
e não deve ser usado para esta cópia.

No SQLite, JSONB usa JSON, UUID usa representação hexadecimal e os IDs usam
INTEGER de 64 bits com geração automática. Campos monetários mantêm o contrato
de duas casas, com a representação NUMERIC do SQLite. Embeddings, se existentes,
são listas JSON; busca vetorial pgvector continua restrita ao PostgreSQL.
Datas seguem o contrato UTC, com leitura sem informação de fuso no SQLite.
O painel e `ipeaia-triagem` usam a cópia definida em `DATABASE_URL` e gravam
`extracoes_ia` nesse mesmo arquivo. A cópia é destinada ao piloto de pesquisa
no ambiente remoto; alterações não sincronizam automaticamente com a origem.
Não executar as migrações Alembic PostgreSQL nesta cópia SQLite.

Todo chunk deve registrar texto, hash, modelo de embedding, dimensão e versão da
pipeline. Toda extração deve registrar modelo, versão do prompt, resultado JSON
e status de validação. A dimensão vetorial será definida quando o modelo for
selecionado; a coluna `vector` aceita armazenamento antes da criação de um índice
específico por dimensão/modelo.

O piloto IpeaIA usa `extracoes_ia.tipo_extracao = triagem_bpc`,
`versao_prompt = bpc_triagem_api_v1.2`, `resultado` JSONB validado e
`status_validacao = pendente`. O modelo vem de `IPEAIA_MODEL` ou `--model`.
Envia apenas campos normalizados de registros públicos TRF1/G1/JE do órgão de
Brasília: classe, órgão, assuntos e movimentos sem complementos, com indicação
de truncamento. Não envia `payload_original`, partes, CPF nem documentos. O
desfecho permanece `indeterminado` sem texto decisório. Cada combinação de
processo, tipo, modelo e versão do prompt é única; uma nova metodologia requer
nova versão, não sobrescrita silenciosa. A classificação não está validada até
revisão humana.

Respostas IpeaIA rejeitadas não são persistidas em `extracoes_ia`. Para diagnóstico,
o CLI salva em `data/ipeaia_rejeitadas/<id>.json` a entrada enviada, modelo,
versão do prompt, erro e resposta da API (token removido). Os arquivos ficam
fora do Git; não são classificações nem registros Bronze imutáveis. O processo
continua pendente. Extrações anteriores v1.0 são preservadas; o prompt v1.1
tem contrato explícito para evidências e não sobrescreve os resultados anteriores.
Por usar outra versão, uma execução v1.1 pode selecionar novamente processos
já triados na v1.0. A versão atual v1.2 também preserva resultados v1.1.

### Resumo natural e contexto TPU — v1.2 (06/10/2026)

`resultado` acrescenta `resumo_caso`: texto obrigatório, não vazio, em português
simples, com até 1500 caracteres. Explica fatos disponíveis, limitações e o que
falta para entender a judicialização, sem inventar negativa administrativa ou
resultado. Os demais campos, incluindo `observacao_curta` (até 300 caracteres),
categorias, evidências e lacunas, são mantidos. O resumo é gerado pela IA e
permanece pendente de revisão humana. É armazenado no mesmo JSON de
`extracoes_ia`, em PostgreSQL ou SQLite; não há tabela nova nem migração.
O painel mostra o resumo e mantém o JSON completo em uma área expansível;
extrações antigas usam `observacao_curta` como alternativa, sem reescrita.

A entrada acrescenta `assuntos[].hierarquia_tpu`, lista de objetos
`{codigo, nome, fonte_arquivo}`, do ancestral ao assunto, consultada em
`referencias_tpu` (`tipo=assunto`). Pais ausentes não são inventados; catálogo
ausente produz lista vazia. Há proteção contra ciclos e limite de 20 níveis.
Na TPU local, 11946 e 11947 descendem de 6114 (Benefício Assistencial), de modo
que seu vínculo temático não deve ser avaliado pelo nome isolado. Isso não
comprova concessão inicial ou motivo da ação. Fonte/contexto ainda é DataJud
mais catálogo TPU; documentos de novas fontes não são enviados neste piloto.
Pendências são selecionadas por modelo e versão; v1.2 pode repetir casos v1.1.

### Decisão sobre conteúdo processual — pesquisa pública de 06/10/2026

Foi confirmada pesquisa pública TRF1/JEF no
[CJF](https://jurisprudencia.cjf.jus.br/trf1/index.xhtml), e o
[TRF1](https://www.trf1.jus.br/trf1/carta-servicos/jurisprudencia) descreve consulta
livre por texto, tipo de documento e fonte TRF1/JEF. O
[arquivo TRF1](https://arquivo.trf1.jus.br/) oferece documentos publicados por
número de processo, não autos completos. Não foi confirmada nesta rodada uma
API pública documentada e viável para baixar em lote petições/sentenças do nosso
recorte. A existência desses portais impede concluir que não há texto público,
mas não demonstra cobertura dos 2.584 processos nem autoriza contornar anti-robô.

O [STJ](https://dadosabertos.web.stj.jus.br/group/jurisprudencia) disponibiliza
espelhos de acórdãos em CSV/JSON/ZIP, com catálogo CKAN testado sem credencial.
É corpus complementar de jurisprudência, selecionado e de outra instância;
não equivale à amostra de concessões iniciais TRF1/DF. Os
[indeferimentos INSS](https://dadosabertos.inss.gov.br/dataset/beneficios-indeferidos-plano-de-dados-abertos-jun-2023-a-jun-2025)
incluem motivos administrativos e ajudam a contextualizar, sem vínculo CNJ
confirmado. Não atribuir esses motivos a um processo individual.

Decisão atual: continuar a triagem de metadados com contexto TPU e resumo
natural para a amostra existente. Foram implementados e testados com respostas
reais os coletores de espelhos STJ, pesquisa CJF/TRF1/JEF1 e agregados INSS.
Na cópia SQLite local, o piloto gravou 5 documentos TRF1, 5 JEF1 e 1 STJ,
sem CNJ coincidente com a amostra (zero vínculos). O arquivo agosto/2026 INSS
foi lido integralmente: 882.589 linhas, 3.258 indeferimentos espécie 87 e 389
espécie 88 no DF, em 24 agregados. Repetição STJ não duplicou versões.
NLP/Métricas ainda não podem atribuir motivos ou desfechos aos 2.584 processos
originais por essas coletas: os textos estão em corpus separado, sem vínculo
e sem envio automático à IpeaIA. Índices de ganho e hipóteses de motivação
exigem conteúdo do próprio processo, recorte e validação humana.

O arquivo TRF1 oscilou entre desafio anti-robô e acesso normal. O JavaScript
público `https://arquivo.trf1.jus.br/js/pages/index.js` usa
`POST https://arquivo.trf1.jus.br/localiza_processo.php`, formulário
`ProcInclui=<CNJ sem pontuação>`, sem credencial. A resposta real para
1053078-37.2022.4.01.3400 e casos de 2009/2013 foi
`{"params":"35","existeProcesso":false,"retorno":"..."}`. Não comprovou
disponibilidade de documentos da amostra. O CLI conserva a resposta e distingue
`sem_resultado`, `bloqueada`, `falhou` e `localizado`; a consulta de disponibilidade
agora pode baixar documentos com `--baixar`, após consulta positiva. A busca
de controle pelo CNJ 0058364-48.2010.4.01.0000, citado em publicação oficial
TRF1, retornou documentos DOC. Esse controle é de outro tema: não entra na
amostra BPC. A listagem vem de `POST /PesquisaMenuArquivo.asp`, com `pN`, `pA`
e `p1` provenientes da resposta de localização; o parser exige que a página
informe o CNJ solicitado no cabeçalho `Processo Pesquisado`.
Só baixa links DOC/TIF/TIFF presentes nessa listagem e do mesmo host HTTPS.
Cada arquivo conserva bytes na Bronze; `documento_id` é seu caminho oficial,
`payload` registra CNJ, tipo, publicação, URL, SHA-256 dos bytes e
`extracao_texto=pendente`. `texto`, `ementa` e `decisao` ficam nulos para arquivo
binário: download não equivale a texto convertido. Foi observado também DOC
que é texto simples, com cabeçalho `TRIBUNAL REGIONAL FEDERAL`, sem bytes NUL.
Nesse caso específico `texto` recebe o conteúdo decodificado estritamente em
UTF-8 (ou Windows-1252 se necessário), com `extracao_texto=texto_simples` e
encoding registrado no payload. Não interpretar HTML de erro como documento.
Não executa Word/macros nem
OCR automaticamente. Conversão DOC é separada, descrita abaixo; TIFF/OCR
continua pendente. Os vínculos usam
somente o CNJ explicitamente confirmado pela listagem, sem criar processos.
Limite de arquivos por execução não comprova cobertura de autos completos.
A API de documentos expõe `extracao_texto` do payload para indicar no painel
arquivo binário ainda pendente versus texto simples já disponível. Não é
validação semântica da decisão ou autorização para transmissão de dados pessoais.

### Extração preliminar de DOC legado

`converter-arquivo-trf1` lê somente arquivos já baixados e confinados à Bronze
local. Exige SHA-256 igual ao registrado, limita arquivo a 16 MiB e texto a
2 MiB, não faz chamadas externas e não abre Word/macros. Usa `legacy-doc`
0.2.1, leitor Python de Word 97–2003; não é renderizador completo e não garante
recuperação de formatação, alterações rastreadas, imagens ou conteúdo integral.
Resultado vazio, caracteres de substituição ou falha rejeitam a conversão.
TIFF permanece pendente de OCR.

Uma conversão aceita cria outra versão em `documentos_publicos`, mantendo ID
da fonte, CNJ confirmado, URL, caminho e hash do arquivo original. Não reescreve
a versão pendente. `texto` recebe texto extraído; ementa/decisão não são
classificadas automaticamente. O payload conserva os campos originais e
acrescenta `extracao_texto=doc_legacy_preliminar`, `parser_texto`,
`versao_parser_texto`, `versao_pipeline_texto=trf1_doc_v1`, `sha256_texto` e
`avisos_extracao` (lista). Não copia autor, empresa ou outros metadados pessoais
do documento para o payload. A coleta usa fonte `trf1_arquivo_conversao`.
Deduplicação considera arquivo + versão da pipeline; mesmo arquivo com dois
registros pendentes é convertido uma vez. Sem `--executar` apenas lista
pendências. O texto não entra automaticamente na IA e permanece não validado.

O CJF foi acessado por formulário JSF público, com ViewState e cookies de sessão,
sem credencial institucional, em TRF1/JEF1. Paginação AJAX foi testada em duas
páginas de 30 documentos, sem IDs repetidos. A interface não é contrato REST
estável; mudança de formulário interrompe a coleta em vez de produzir falso vazio.
Não afirmar completude de cobertura. No STJ, os números curtos de recurso e
registro não são CNJ; não criar vínculo automático nem converter número curto.

`cjf-amostra` pesquisa CNJs da base já existente, sem ampliar a população.
Seleção automática: registros públicos (sigilo 0 ou ausente), TRF1/G1/JE,
código municipal DataJud do órgão `743` e assuntos 6114/11946/11947. Isso é
recorte do órgão, não residência nem prova de concessão inicial. A pesquisa
livre aceita CNJ sem pontuação; o portal rejeita hífen no texto livre.
Cada tentativa usa `coletas.fonte=cjf_amostra`, partição
`<CNJ>/<TRF1 ou JEF1>/p<limite de páginas>/v1`, parâmetros com consulta e versão
`cnj_digitos_v1`. Sucessos e vazios da mesma configuração são pulados no
próximo lote; `--retentar` permite nova consulta. Erros/bloqueios continuam
pendentes, interrompem o lote e não são tratados como vazio. O vazio significa
somente ausência de resultado nessa fonte/configuração, não inexistência do
processo. O limite de páginas não comprova cobertura completa.
Documentos são guardados no corpus existente, mas somente CNJ estruturado
coincidente permite vínculo. Uma menção ao número no texto não cria vínculo.
Nenhuma nova tabela ou envio automático à IA é introduzido.

O parser aceita JSON puro ou um único bloco Markdown JSON, sem texto externo.
Caminhos de evidência como `classe.nome`, `assuntos[0].nome` e
`movimentacoes[sequencia=12].nome` são aceitos somente quando existem no registro
efetivamente enviado (não no conjunto de movimentos omitidos por truncamento).
O objeto de evidência mantém exatamente `registro_id` (inteiro da entrada),
`campo`, `referencia` e `sustenta` (textos). `campo` pode ser `classe`, `assuntos`,
`movimentacoes`, `orgao_julgador`, `tribunal` ou `grau`. Os dois últimos são
metadados já enviados e apoiam recorte jurisdicional, não mérito ou residência.
Caminhos são normalizados para o campo raiz, mantendo o caminho original como
prefixo de `referencia`. Referências compostas separadas por `/`, como
`tribunal / grau`, geram uma evidência por componente, somente se TODOS forem
campos/caminhos permitidos e presentes no registro enviado. Preservam o ID,
o texto de sustentação e a referência original com prefixo do campo composto.
Componentes inexistentes ou vazios rejeitam toda a resposta; nenhuma evidência
é descartada silenciosamente. Não há mudança de tabelas ou colunas.
Isso valida estrutura e vínculo, não a interpretação factual, ainda pendente
de revisão humana. Quando a API informa modelo diferente do solicitado, a
resposta é rejeitada com diagnóstico para não registrar atribuição incorreta.

## Regras de qualidade

- Datas normalizadas em UTC;
- Payload Bronze nunca corrigido ou sobrescrito;
- Correção de encoding somente nos campos normalizados;
- Checkpoint avança após Bronze e transação PostgreSQL;
- `sem_resultado` no Comunica é diferente de erro;
- Reexecução não cria duplicatas;
- Não inferir concessão ou negativa apenas pela existência do movimento Sentença;
- Não versionar chaves nem o `.env`. Para a transferência solicitada pelo
  pesquisador, `transferencia/bpc-jud.dump` e `transferencia/bpc-jud.sqlite.gz`
  são as cópias autorizadas a
  acompanhar o Git, sem criptografia. Arquivos Bronze continuam fora do Git.

## Log de decisões

| Data | Decisão | Consequência |
| --- | --- | --- |
| 06/10/2026 | Integrar concessões públicas INSS como contexto agregado por competência/UF/espécie/despacho, em tabela separada. | Leitura integral agosto/2026: 848.996 linhas; DF: 1.495 concessões espécie 87 e 667 espécie 88, em 8 grupos. Não são processos, pessoas únicas nem taxa de procedência. Microdados só na Bronze local; sem vínculo CNJ nem envio à IA. Contrato v0.7/migração aditiva 0007. |
| 06/10/2026 | Extrair preliminarmente texto de DOC legado em Python, sem Office/macros/rede e sem sobrescrever originais. | Dois arquivos reais convertidos: ementa BPC (3.217 caracteres) e decisão de controle (5.614). Leitor/pipeline/hash registrados; revisão humana e integridade textual não comprovadas. TIFF/OCR pendente. Não altera recorte nem envia textos à IA. |
| 06/10/2026 | Implementar download do arquivo TRF1 após localização positiva e confirmação do CNJ na listagem. | Controle positivo oficial e um BPC de 2006 no Piauí foram localizados; certidão/ementa deste último geraram dois vínculos reais com a base. Ele fica fora DF/RIDE. Texto simples lido; DOC binário/OCR TIFF pendentes, sem envio à IA. Originais preservados localmente. |
| 06/10/2026 | Acrescentar busca CJF por CNJ da própria amostra, com lote retomável e vazio separado de erro. | Consulta real: 10 TRF1 e 5 JEF1 sem resultados nos limites pedidos; busca de controle com CNJ já conhecido retornou documento. Repetição JEF1 avançou aos próximos pendentes. Não comprova falta de decisões nos autos. Vínculo exige CNJ estruturado; textos seguem locais e não são enviados à IA. |
| 06/10/2026 | Incluir histórico ZIP oficial STJ mediante `--historico`, com Bronze antes do parsing e limites de expansão. | Coleta real acrescentou 20 documentos históricos da Segunda Turma; corpus local agora tem 31 documentos (21 STJ, 10 CJF), zero vínculos. Os 2.584 processos originais não foram alterados. Histórico completo e textos do recorte continuam não comprovados; sem envio automático à IA. |
| 06/10/2026 | Integrar corpus CJF/TRF1/JEF1 e STJ, e agregados de indeferimentos INSS; testar disponibilidade no arquivo TRF1 sem bypass. | 11 documentos e 24 agregados reais na cópia SQLite local; zero vínculos com a amostra. Novas tabelas aditivas, origem/versões preservadas. Arquivo TRF1 sem documentos localizados nos CNJs testados; download DOC/TIFF segue pendente. Dados novos não acompanham o Git nem entram automaticamente na IpeaIA. |
| 06/10/2026 | Prompt v1.2: resumo natural no JSON de extração e hierarquia TPU na entrada; pesquisar fontes públicas adicionais sem misturar populações. | `resumo_caso` até 1500 caracteres, sem novas tabelas; resultados anteriores preservados. CJF/TRF1 e STJ são fontes candidatas de textos, mas nenhuma coleta de documentos foi integrada. Motivo e desfecho continuam não determináveis só por metadados. |
| 06/10/2026 | Versionar prompt de triagem em v1.1, explicitar formato e permitir evidências de `tribunal`/`grau`; desmembrar campos compostos válidos separados por `/`. | Resolve incompatibilidade `tribunal / grau` sem inventar vínculos nem descartar evidências. Metadados já enviados; enum de `campo` ampliado neste contrato. Resultados v1.0 preservados e passíveis de nova triagem v1.1. |
| 05/10/2026 | Aceitar bloco Markdown JSON e normalizar caminhos de evidências existentes; rejeitar divergência de modelo informada pela API. | Corrige incompatibilidade de formato observada no GLM sem aceitar caminhos inexistentes nem mudar o schema; preserva rastreabilidade do caminho em `referencia`. |
| 05/10/2026 | Detalhar falhas de evidências e conservar respostas IpeaIA rejeitadas em diagnóstico local sem token. | Não aceitar IDs externos à entrada nem formatos inválidos; nenhuma extração gravada para o processo rejeitado e nenhuma repetição automática. Sem alteração do schema ou da metodologia. |
| 05/10/2026 | Usar cópia SQLite no servidor remoto sem Docker; manter PostgreSQL na origem. | Mesmas tabelas e dados da aplicação; extrações de IA persistidas em `data/bpc-remote.sqlite`. Embeddings armazenados como JSON, sem busca pgvector nesse ambiente. |

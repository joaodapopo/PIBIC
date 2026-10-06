# Fontes abertas adicionais — avaliação inicial (22/09/2026)

O recorte principal continua sendo o de [ESCOPO_PESQUISA.md](ESCOPO_PESQUISA.md).
Estas fontes não substituem o DataJud e não devem ser ligadas a uma pessoa ou
processo sem identificador e autorização adequados.

## DJEN / Comunica PJe

- A consulta pública `GET /api/v1/comunicacao` por número CNJ, já implementada,
  respondeu sem autenticação. No piloto de 50 processos ainda não consultados,
  estratificados em 10 por ano de ajuizamento de 2021 a 2025 no recorte
  TRF1/órgão de Brasília/G1 ou JE, 3 tiveram resultado e 47 não tiveram.
  Foram persistidas 7 comunicações (intimações, despacho e ato ordinatório),
  nenhuma tipificada como sentença. O piloto não estima cobertura universal.
- A [API pública documenta](https://hcomunicaapi.cnj.jus.br/swagger/djen.yml)
  busca por tribunal, data e texto, além do caderno por tribunal/data/meio.
  A consulta experimental de texto `BPC` retornou HTTP 500; não foi usada
  para ingestão. Um caderno TRF1/D de 21/09/2026 respondeu sem credencial,
  mas anuncia 51.030 comunicações e 197.903.371 bytes. A ingestão indiscriminada
  de cadernos inteiros não se justifica antes de medir a cobertura e o custo.
- Comunicações são atos publicados, não inteiro teor dos autos. O endpoint
  `POST` e a autenticação do CNJ são de uso dos tribunais, não deste projeto.

## TRF1

- A [consulta processual antiga](https://processual.trf1.jus.br/consultaProcessual/)
  apresenta metadados e, em alguns processos, links de “Inteiro Teor”. Uma
  tentativa de consulta automatizada de um processo de 2018 do nosso recorte
  recebeu a página de verificação anti-robô. Não contornar essa proteção.
- O [PJe atual](https://www.trf1.jus.br/trf1/processual/consulta-processual)
  limita a consulta pública, e não foi identificada API pública de coleta em
  lote de sentenças. Acesso sistemático a documentos exige diálogo institucional
  com o tribunal. Uma amostra manual autorizada pode medir a disponibilidade.

## INSS — bases mensais

- O catálogo oficial [CKAN do INSS](https://dadosabertos.inss.gov.br/) responde
  sem credencial. As buscas por “beneficios indeferidos” e “beneficios concedidos”
  catalogaram respectivamente 102 e 106 recursos nesta rodada. Esses números
  contam metadados de arquivos, não benefícios importados. A tabela
  `recursos_externos` contém o catálogo e mantém URL e metadados da fonte.
- No arquivo [Benefícios Indeferidos — julho/2026](https://dadosabertos.inss.gov.br/dataset/beneficios-indeferidos-plano-de-dados-abertos-jun-2023-a-jun-2025),
  a leitura experimental encontrou 935.123 linhas. Para `UF = Distrito Federal`,
  foram 5.200 indeferimentos da espécie 87 (BPC pessoa com deficiência) e 462
  da 88 (BPC idoso). Há coluna de motivo, mas não de número CNJ nem município
  de residência nessa planilha. Esses totais **não** representam processos nem
  requerentes únicos e não devem ser comparados diretamente à amostra DataJud.
  Não houve persistência de microdados; a cópia temporária foi removida.
- Próxima implementação: importador de indicadores agregados por competência,
  UF, espécie e motivo, com hash e URL do arquivo de origem, dicionário de
  categorias e controle de qualidade. Criar o contrato da nova tabela antes da
  migração. Para análise municipal, usar fonte que explicite a geografia e sua
  unidade (residência, APS ou município pagador).

## Portal da Transparência/CGU — integrado

- O pesquisador configurou localmente o token da
  [API do Portal da Transparência](https://portaldatransparencia.gov.br/api-de-dados).
  Ele permanece no `.env` e não deve ir ao Git nem a mensagens. A coleta usa
  `GET /api-de-dados/bpc-por-municipio` com `mesAno`, `codigoIbge` e `pagina`,
  autenticado pelo cabeçalho `chave-api-dados`.
- Uma chamada real para Brasília (`5300108`) em julho/2026 retornou
  `quantidadeBeneficiados = 70480` e `valor = 131276767.55`, além de
  `dataReferencia`, município e tipo BPC. O comando `transparencia-bpc` grava
  a resposta original na Bronze e o indicador em `indicadores_bpc_municipio`.
  A série de Brasília de janeiro/2019 a julho/2026 foi coletada: 91 competências,
  todas com resposta, e uma reexecução não duplicou a competência já existente.
  Esses dados são agregados mensais; não representam novas concessões,
  pessoas identificadas nem decisões judiciais. Não equiparar o município
  retornado ao município do órgão julgador do DataJud sem validação geográfica.

## Fontes que exigem participação do pesquisador
- Dados individuais do INSS/CadÚnico ou inteiro teor em lote no PJe exigem
  autorização institucional e revisão da governança de dados. Não presumir que
  o caráter público de um processo autoriza extração irrestrita de seus dados.

## Reavaliação: documentos públicos e códigos — 06/10/2026

### O que pode melhorar a qualidade

| Fonte | Conteúdo confirmado | Acesso e limite observado |
| --- | --- | --- |
| [Jurisprudência TRF1/JEF — CJF](https://jurisprudencia.cjf.jus.br/trf1/index.xhtml) | Pesquisa de acórdãos, decisões monocráticas, súmulas e arguições, com fontes TRF1/JEF1. | Página pública acessível; [carta de serviços TRF1](https://www.trf1.jus.br/trf1/carta-servicos/jurisprudencia) informa consulta livre. Não foi comprovada API documentada de coleta em lote nem cobertura de processos da amostra. |
| [Arquivo de inteiro teor TRF1](https://arquivo.trf1.jus.br/) | Busca por número de processo de documentos publicados; o portal informa DOC e TIFF. | Formulário público confirmado, não autos completos. Disponibilidade de documento para cada CNJ da amostra ainda não medida. A [FAQ TRF1](https://www.trf1.jus.br/trf1/ouvidoria/perguntas-frequentes) descreve inteiro teor de processos físicos. |
| [STJ — espelhos de acórdãos](https://dadosabertos.web.stj.jus.br/group/jurisprudencia) | Catálogo de jurisprudência em CSV/JSON/ZIP; espelhos, não garantia de íntegra de autos. | `GET https://dadosabertos.web.stj.jus.br/api/3/action/package_search?q=jurisprudencia&rows=1` testado sem credencial: retornou o conjunto `espelhos-de-acordaos-corte-especial`, formatos CSV/JSON/ZIP. Arquivos não foram importados. |
| [INSS — indeferimentos](https://dadosabertos.inss.gov.br/dataset/beneficios-indeferidos-plano-de-dados-abertos-jun-2023-a-jun-2025) | Competência, espécie, motivo de indeferimento, UF e outros campos administrativos. | Recursos públicos mensais; contexto de negativas administrativas, não ligação comprovada com ações judiciais. Não atribuir motivo agregado a uma pessoa/processo. |

O [dataset Segunda Turma do STJ](https://dadosabertos.web.stj.jus.br/dataset/espelhos-de-acordaos-segunda-turma)
descreve seleção técnico-documentária de acórdãos, histórico inicial e atualizações,
com possível repetição de IDs entre arquivos. Um importador precisa deduplicar por
ID e preservar origem, data e corpus. Não misturar a seleção recursal STJ com
a população de concessões iniciais TRF1/DF nem calcular taxa de procedência
do recorte com esses julgados.

**Prioridade proposta:** piloto separado de documentos TRF1/JEF; STJ como corpus
complementar para fundamentos e construção de categorias; INSS para contexto.
Termos de pesquisa: `"benefício assistencial"`, `"benefício de prestação continuada"`,
`BPC`, `LOAS`, `"art. 203"`. Pesquisar número CNJ quando disponível, sem presumir
que um acórdão de tribunal superior corresponda automaticamente ao CNJ de origem.
Respeitar limites de acesso; não contornar CAPTCHA ou anti-robô. Nenhuma dessas
novas fontes de documentos foi integrada à triagem nesta atualização.

### Códigos de assunto

A TPU local em `datajud/datajud/tb_CNJ/OUTPUT/Assuntos/assuntos_JF1G.csv` mantém
`6114` (Benefício Assistencial) e filhos `11946` (Pessoa com Deficiência) e
`11947` (Idoso). Não foi identificado no snapshot outro filho direto de 6114.
O [CNJ — Justiça em Números 2023](https://bibliotecadigital.cnj.jus.br/bitstream/123456789/727/1/justica_em_numeros_2023_010923__1_.pdf)
também apresenta esses códigos e o vínculo entre benefício assistencial e seus
subassuntos. A consulta on-line SGT retornou 403 nesta verificação; não afirmar
que o snapshot local é a versão mais recente da tabela.

`6117` — Renda Mensal Vitalícia aparece no catálogo e em
[consulta pública TRF1](https://processual.trf1.jus.br/consultaProcessual/processo.php?proc=00000029220194019197&secao=TRF1).
É candidato a estudo complementar, não substituto nem ampliação automática do
recorte BPC. Assuntos amplos de revisões, renda inicial ou assistência misturam
benefícios e pedidos fora do escopo. Os comandos continuam limitados aos três
códigos originais; ampliar a amostra por período/tribunal requer explicitar a
mudança de população, em vez de acrescentar códigos genéricos indiscriminadamente.

### Decisão para o processamento atual

Não declarar ausência de texto público: há portais de decisões e uma base STJ
em formato aberto. Tampouco prometer acesso em lote a petições e sentenças do
nosso recorte. Até testar/importar documentos com vínculo e origem, a triagem
permanece DataJud + TPU, agora com resumo natural, e não identifica motivo da
judicialização ou resultado individual. Ver contrato v1.2 em `CONTRATOS.md` e
comando de lote em `SEM_DOCKER.md`.

## Implementação e evidência de coleta — 06/10/2026

- **CJF/TRF1/JEF1:** formulário público JSF (`formulario:textoLivre`,
  `formulario:selectTiposDocumento`, checkbox da base e ViewState), cookies de
  sessão e paginação AJAX. Acórdãos/decisões publicados ficam em
  `documentos_publicos`, com ID da origem, campos, texto, hashes e Bronze HTML/XML.
  Piloto persistiu 5 TRF1 e 5 JEF1. Duas páginas JEF1 retornaram 30 + 30 IDs sem
  sobreposição. HTTP 504 ocorreu em uma consulta mais pesada e ficou como erro,
  não ausência de dados. Nenhum documento foi vinculado por similaridade.
- **STJ:** download de recursos JSON recentes selecionados pelo CKAN, filtrados
  por termos explícitos BPC/LOAS/benefício assistencial; ID da origem e conteúdo
  são versionados. JSON agosto/2026 da Segunda Turma retornou 1 candidato, gravado;
  repetir gravou zero versões novas. O número curto `3126923` não foi convertido
  nem unido a CNJ. A opção `--historico` agora inclui ZIP oficial antes dos
  recursos recentes, preservando seus bytes e lendo membros JSON sem extração
  no disco. O ZIP histórico real da Segunda Turma tem 95.529.644 bytes e cinco
  JSONs (413.639.349 bytes expandidos), até maio/2022. Limites: 128 MiB de
  download/por membro e 1 GiB expandido total. Limitar a coleta não demonstra
  cobertura completa, e repetir o mesmo limite não avança um checkpoint.
  A execução real com `--historico --limit 20` terminou como `concluida` e
  gravou 20 versões novas. Corpus local: 21 STJ + 10 CJF; a amostra permanece
  com 2.584 processos e zero vínculos documentais.
- **INSS:** recurso oficial de agosto/2026 (XLSX, 63.610.786 bytes) lido integralmente
  em modo streaming. Título/cabeçalho e par de colunas espécie código/descrição
  foram tratados pelo layout observado. 882.589 linhas lidas; DF: 3.258 espécie 87,
  389 espécie 88, em 24 grupos de motivo. Só agregados são gravados na Silver.
  XLS histórico e conjuntos de concessões ainda requerem importadores próprios.
- **Arquivo TRF1:** acesso variou entre Cloudflare e formulário acessível. O endpoint
  público usado pelo próprio script `js/pages/index.js` foi testado:
  `POST /localiza_processo.php`, campo `ProcInclui` com CNJ sem pontuação. Sem
  credencial. CNJs testados (2022, 2009, 2013) retornaram `existeProcesso:false`.
  CLI `arquivo-trf1 --baixar` agora guarda a listagem e baixa DOC/TIFF a partir
  dos links publicados. Controle positivo de outro tema: CNJ
  0058364-48.2010.4.01.0000, citado na publicação oficial
  [Sequestro Internacional Parental](https://www.trf1.jus.br/trf1/conteudo/files/Sequestrointernacionalparental.pdf).
  Um BPC da própria base, 0003970-58.2006.4.01.4001 (Piauí, fora DF/RIDE),
  também foi localizado: certidão em texto simples e ementa DOC binária foram
  baixadas e vinculadas. São os primeiros dois vínculos reais dessa fonte.
  Conversão DOC binário/OCR TIFF permanece pendente; certidão com cabeçalho
  conhecido e extensão DOC, mas bytes de texto simples, foi decodificada sem OCR.
  A API/painel distingue `extracao_texto=pendente` de `texto_simples`.
  Não houve bypass,
  envio de e-mail automático ou uso de credencial institucional.
- **MCP DeHor:** o [README atual](https://github.com/DeHor-Labs/mcp-juridico-brasil)
  descreve DataJud e ferramentas de monitoramento/prazos, não nova base de inteiro
  teor. Não foi instalado: reutilizar o que já temos não resolve cobertura textual.

O comando `cjf-amostra` consulta candidatos BPC públicos TRF1/G1/JE de órgãos
de Brasília usando os números da base, sem pontuação. O portal rejeita hífen
na pesquisa livre. Teste de controle com número de documento já conhecido
retornou um documento; consultas reais de 10 candidatos no TRF1 e 5 no JEF1
retornaram zero. Esses vazios ficam em `coletas` e a próxima execução avança
na mesma base/configuração; erros/bloqueios não viram vazio. Isso não comprova
inexistência de documentos no processo. O comando não depende da triagem IA.

O pacote SQLite no Git permanece o original; os dados novos estão só em
`data/bpc-remote.sqlite` local e são reproduzíveis pelos comandos de coleta no
ambiente remoto. Não enviar os textos/microdados brutos ao Git nem à API IA
automaticamente. O painel `/admin/documentos` exibe corpus e agregados separados.

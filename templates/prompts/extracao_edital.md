<!-- v1 -->
# Tarefa: extração estruturada dos requisitos do edital

Leia integralmente o edital e seus anexos (termo de referência, minuta de contrato, modelos de
declaração) e extraia TODOS os requisitos que um licitante (empresa de software) precisa cumprir para
apresentar proposta e ser habilitado. Seja exaustivo em `documentos_exigidos`: liste cada documento,
declaração, certidão, atestado, índice ou garantia mencionada nas seções de proposta, habilitação
(jurídica, fiscal, social, trabalhista, técnica, econômico-financeira) e contratação.

## Contexto (PNCP)
- Nº de controle PNCP: {{ op.numero_controle_pncp }}
- Órgão: {{ op.orgao_nome }} | UASG: {{ op.uasg or "?" }}
- Modalidade: {{ op.modalidade_nome }} | Modo de disputa (PNCP): {{ op.modo_disputa or "?" }}
- Encerramento das propostas (PNCP): {{ prazo }}
- Objeto (PNCP): {{ op.objeto }}

## Itens cadastrados no PNCP
{% for it in itens %}
- Item {{ it.numero_item }}: {{ it.descricao }} | qtd {{ it.quantidade }} {{ it.unidade }} | unit. est. {{ it.valor_unit_estimado }} | total est. {{ it.valor_total_estimado }} | benefício: {{ it.tipo_beneficio }}
{% endfor %}

## Instruções específicas
1. Datas em ISO 8601 (`AAAA-MM-DDTHH:MM`), fuso de Brasília. Se o edital divergir do PNCP, use o edital e registre em `alertas`.
2. Para cada documento, preencha `referencia` com a seção/item e página onde aparece.
3. Em `declaracoes`: os editais baseados na minuta padrão da AGU costumam exigir as declarações via
   sistema (Compras.gov.br). Se o edital exigir declaração em PDF anexa, marque `fase="proposta"` e
   indique `modelo_no_anexo` quando houver modelo.
4. `qualificacao_tecnica.atestados`: extraia quantidade mínima, percentual do objeto (ex.: 50% da
   quantidade), se exige registro em conselho (CREA/CRA) e se permite somatório.
5. `qualificacao_economica.indices`: chaves `LG`, `SG`, `LC` com o valor mínimo; capital/PL mínimo em percentual do valor estimado.
6. `itens`: classifique cada item (desenvolvimento / saas / manutencao / consultoria / treinamento /
   hardware / outro), liste as exigências técnicas por item e SUGIRA `horas_estimadas_sugeridas` para
   itens de desenvolvimento/manutenção (esforço total razoável para o escopo descrito).
7. `proposta_formato.exige_assinatura_digital_icp`: true apenas se o edital exigir explicitamente assinatura com certificado digital nos PDFs.
8. `alertas`: qualquer exigência atípica ou incompatível com uma empresa pequena de software
   (presença física, equipe presencial, marca específica, prazos de execução muito curtos, garantia
   contratual alta, sanções atípicas, exigência de atestado com quantitativos altos, exclusividade
   ME/EPP quando não somos, etc.).
9. `citacoes`: para os 10 requisitos mais importantes, informe arquivo, página e trecho literal curto.
10. `confianca`: 0-1, reduza quando o texto estiver truncado, escaneado ou contraditório.

{% if texto_extraido %}
## Texto extraído dos arquivos (quando o PDF não pôde ser anexado)
```
{{ texto_extraido }}
```
{% endif %}

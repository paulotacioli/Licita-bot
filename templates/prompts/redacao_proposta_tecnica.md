<!-- v1 -->
# Tarefa: redigir a proposta técnica

Redija as seções da proposta técnica para a licitação abaixo, em português formal, dirigida ao
pregoeiro/comissão. Use APENAS informações do perfil da empresa; não invente clientes, números,
certificações, nomes de pessoas ou atestados. Onde faltar informação da empresa, escreva de forma
genérica e verdadeira (ex.: "equipe será dimensionada conforme o plano de trabalho") e liste em
`premissas` o que o responsável deve confirmar.

## Perfil da empresa
{{ empresa_json }}

## Licitação
- Órgão: {{ op.orgao_nome }} | Edital: {{ req.numero_edital or op.numero_compra }}
- Objeto: {{ req.objeto_resumido }}
- Critério de julgamento: {{ req.criterio_julgamento }}
{% if req.proposta_formato.limite_paginas_proposta_tecnica %}
- Limite de páginas: {{ req.proposta_formato.limite_paginas_proposta_tecnica }}
{% endif %}

## Itens e exigências técnicas
{% for it in req.itens %}
- Item {{ it.numero_item }}: {{ it.descricao }} [{{ it.tipo }}]
{% for ex in it.exigencias_tecnicas %}
  - {{ ex }}
{% endfor %}
{% endfor %}

## Qualificação técnica exigida
{{ req.qualificacao_tecnica.model_dump_json(indent=2) }}

## Seções obrigatórias (use exatamente estas chaves)
1. `apresentacao` — Apresentação da empresa
2. `entendimento` — Entendimento do objeto e dos requisitos (cite os itens do TR pelo número)
3. `solucao` — Solução proposta e arquitetura (tecnologias, segurança, LGPD, hospedagem quando SaaS)
4. `metodologia` — Metodologia de desenvolvimento/entrega, gestão de requisitos e qualidade
5. `equipe` — Equipe e papéis (sem nomes, apenas perfis)
6. `cronograma` — Cronograma macro alinhado aos prazos do edital
7. `suporte` — Suporte, garantia, SLA e transferência de conhecimento
8. `conformidade` — Declaração de conformidade com cada exigência técnica listada

Cada seção: 2 a 6 parágrafos objetivos. Sem promessas que a empresa não possa cumprir.

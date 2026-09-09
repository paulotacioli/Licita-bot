<!-- v1 -->
# Tarefa: triagem de relevância

Avalie se a licitação abaixo é uma oportunidade adequada para a empresa descrita, que atua como
**fábrica de software (desenvolvimento sob demanda, sustentação, integração)** e **licenciadora de
produto SaaS próprio**. Não somos revendedores de hardware nem de licenças de terceiros
(Microsoft, Oracle, antivírus etc.), salvo quando o item principal for desenvolvimento/licenciamento
de solução própria.

## Perfil resumido da empresa
{{ empresa_resumo }}

## Dados da licitação (PNCP)
- Órgão: {{ op.orgao_nome }} ({{ op.esfera }} / {{ op.uf }} / {{ op.municipio }})
- Modalidade: {{ op.modalidade_nome }} | Modo de disputa: {{ op.modo_disputa or "?" }} | SRP: {{ "sim" if op.srp else "não" }}
- Valor estimado: {{ valor_estimado }}
- Encerramento das propostas: {{ prazo }}
- Objeto: {{ op.objeto }}

## Itens
{% for it in itens %}
- Item {{ it.numero_item }}: {{ it.descricao }} | qtd {{ it.quantidade }} {{ it.unidade }} | unit. est. {{ it.valor_unit_estimado }} | benefício: {{ it.tipo_beneficio }}
{% endfor %}

## Trecho inicial do edital / termo de referência
```
{{ trecho }}
```

## Critérios
- `categoria`: desenvolvimento (sistema sob medida, fábrica de software, pontos de função, horas),
  licenciamento_saas (licença de uso de sistema com hospedagem/serviço, "locação de software"),
  manutencao (sustentação/evolução de sistema existente), outsourcing (alocação de mão de obra/postos
  de trabalho), hardware_misto (software é acessório de hardware), irrelevante.
- `score` alto (80-100): objeto central é software sob medida ou licença de sistema que poderíamos
  desenvolver/entregar; sem exigência de produto de fabricante específico; sem presença física obrigatória.
- `score` médio (40-79): software é relevante mas há exigências fortes (marca específica, atestado muito
  específico, equipe presencial, prazo curtíssimo, integração com sistema proprietário fechado).
- `score` baixo (0-39): hardware, licenças de terceiros, outsourcing de postos, objeto fora de TI.
- `exige_atestado_especifico`: true se o edital pede atestado de objeto muito particular
  (ex.: "sistema de gestão hospitalar para 500 leitos").
- Marque `relevante=true` somente se score >= 60.

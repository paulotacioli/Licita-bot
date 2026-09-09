<!-- v1 -->
# Tarefa: pré-triagem de licitações pelo objeto

Você vai receber uma lista de licitações com apenas o resumo que o PNCP publica (órgão, modalidade,
valor e objeto). O edital ainda não foi baixado. Classifique cada uma em relação ao perfil da empresa.

## Perfil da empresa
{{ empresa_resumo }}

## O que a empresa BUSCA (escrito pelo dono, em linguagem natural)
{{ perfil.buscamos or "(não informado)" }}

## O que a empresa EVITA
{{ perfil.evitamos or "(não informado)" }}

## Como decidir
- `relevante`: pelo objeto, tem boa chance de ser o que a empresa busca e não bate em nada que ela evita.
- `irrelevante`: o objeto claramente é outra coisa (hardware, obras, alimentos, serviços sem software,
  revenda de licença de terceiro etc.) ou bate de frente com o que a empresa evita.
- `incerto`: o objeto é vago ou mistura coisas; vale baixar o edital para decidir.
- Quando o objeto não diz nada sobre uma exigência do perfil (por exemplo, presença física), NÃO presuma:
  isso será verificado depois no edital. Julgue só o que está escrito.
- Só o resumo do PNCP é dado; não invente detalhes.

Responda com um item para CADA referência recebida, na mesma ordem, copiando o número `ref`.

## Licitações
{% for it in itens %}
### ref {{ it.ref }}
- Órgão: {{ it.orgao }} ({{ it.uf }}) · {{ it.modalidade }} · valor: {{ it.valor }}
- Objeto: {{ it.objeto }}
{% endfor %}

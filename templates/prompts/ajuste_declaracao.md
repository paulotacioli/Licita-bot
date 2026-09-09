<!-- v1 -->
# Tarefa: adequar declaração ao modelo do edital

O edital traz um modelo próprio de declaração. Produza o texto integral da declaração, seguindo o
modelo do edital palavra por palavra no que for exigência, apenas substituindo os campos variáveis
pelos dados da empresa abaixo. Não acrescente compromissos além dos do modelo. Liste em
`diferencas_vs_modelo_padrao` o que difere da declaração padrão da AGU (se houver).

## Dados da empresa
- Razão social: {{ empresa.razao_social }}
- CNPJ: {{ empresa.cnpj }}
- Endereço: {{ empresa.endereco }}, {{ empresa.cidade }}/{{ empresa.uf }}, CEP {{ empresa.cep }}
- Representante: {{ empresa.representante.nome }}, CPF {{ empresa.representante.cpf }}, {{ empresa.representante.cargo }}
- Porte: {{ empresa.porte }}

## Licitação
- Órgão: {{ op.orgao_nome }} | Edital/Pregão: {{ numero_edital }} | UASG: {{ op.uasg }}
- Local e data: {{ empresa.cidade }}, {{ data_extenso }}

## Tipo de declaração solicitada
{{ tipo }}

## Modelo encontrado no edital
```
{{ modelo }}
```

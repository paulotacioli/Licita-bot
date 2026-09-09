# Cofre de documentos

Coloque aqui os documentos REAIS da empresa. Esta pasta não é versionada.

## Convenção de nomes

`<tipo>_<AAAA-MM-DD>[_descricao].pdf` — a data é a de **emissão** do documento.

Exemplos:
- `contrato_social_2024-03-10.pdf`
- `cartao_cnpj_2026-09-01.pdf`
- `documento_representante_2020-01-01.pdf`
- `certidoes/cnd_federal_2026-08-20.pdf`
- `certidoes/crf_fgts_2026-09-01.pdf`
- `certidoes/cndt_2026-08-20.pdf`
- `certidoes/cnd_estadual_2026-08-15.pdf`
- `certidoes/cnd_municipal_2026-08-15.pdf`
- `certidoes/certidao_falencia_2026-08-15.pdf`
- `certidoes/simples_nacional_2026-09-01.pdf`
- `balanco/balanco_patrimonial_2026-04-30_exercicio_2025.pdf`
- `atestados/atestado_capacidade_tecnica_2025-11-20_prefeitura_x_sistema_protocolo.pdf`
- `assinatura_2026-01-01.png` (assinatura do representante, fundo transparente)
- `logo_2026-01-01.png`

## Validade padrão (dias a partir da emissão)

| Tipo | Validade | Onde emitir |
|---|---|---|
| cnd_federal | 180 | https://solucoes.receita.fazenda.gov.br/Servicos/certidaointernet/PJ/Emitir |
| crf_fgts | 30 | https://consulta-crf.caixa.gov.br/consultacrf/pages/consultaEmpregador.jsf |
| cndt | 180 | https://cndt-certidao.tst.jus.br/inicio.faces |
| cnd_estadual | 90 (varia) | SEFAZ do seu estado |
| cnd_municipal | 90 (varia) | Prefeitura da sede |
| certidao_falencia | 90 (varia) | Tribunal de Justiça do estado |
| cartao_cnpj | 90 | https://solucoes.receita.fazenda.gov.br/servicos/cnpjreva/cnpjreva_solicitacao.asp |
| sicaf | 30 | Compras.gov.br → SICAF → Declaração |

Para sobrescrever tipo/validade de um arquivo, crie `index.yaml`:

```yaml
documentos:
  "cnd_estadual_2026-08-15.pdf":
    valido_ate: 2026-12-15
    orgao_emissor: "SEFAZ-SP"
```

Rode `licitabot cofre scan` depois de adicionar arquivos.

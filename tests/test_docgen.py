from pathlib import Path

from docx import Document

from licitabot.docs.build_templates import build_all
from licitabot.docs.declaracoes import DECLARACOES
from licitabot.pipeline.docgen import fmt_brl, por_extenso


def test_por_extenso():
    assert por_extenso(1.0) == "um real"
    assert por_extenso(100.0) == "cem reais"
    assert por_extenso(1234.56) == "mil duzentos e trinta e quatro reais e cinquenta e seis centavos"
    assert por_extenso(1050.0) == "mil e cinquenta reais"
    assert por_extenso(2_000_000.0) == "dois milhões reais"
    assert por_extenso(2_300_000.0) == "dois milhões e trezentos mil reais"
    assert por_extenso(2_300_500.0) == "dois milhões, trezentos mil e quinhentos reais"
    assert por_extenso(97565.52).startswith("noventa e sete mil")


def test_fmt_brl():
    assert fmt_brl(1234567.891) == "1.234.567,89"
    assert fmt_brl(None) == "-"


def test_templates_render(tmp_path):
    from docxtpl import DocxTemplate

    tpls = build_all(tmp_path)
    assert {p.name for p in tpls} >= {"declaracao.docx", "proposta_comercial.docx", "proposta_tecnica.docx"}
    ctx = {
        "empresa": {"razao_social": "ACME LTDA", "cnpj": "00.000.000/0001-00", "endereco": "Rua A, 1", "cidade": "São Paulo", "uf": "SP", "cep": "01000-000", "telefone": "", "email": "", "site": "", "representante": {"nome": "Fulano", "cargo": "Sócio", "cpf": "000"}, "banco": {"banco": "x", "agencia": "1", "conta": "2", "tipo": "cc"}},
        "op": type("O", (), {"orgao_nome": "Prefeitura X"})(),
        "numero_edital": "Pregão Eletrônico nº 1/2026",
        "objeto": "Sistema",
        "data_extenso": "1 de janeiro de 2026",
        "itens": [{"numero_item": 1, "descricao": "Dev", "unidade": "h", "quantidade_fmt": "10", "marca_modelo": "Serviço", "valor_unit_fmt": "100,00", "valor_total_fmt": "1.000,00"}],
        "total_fmt": "1.000,00", "total_extenso": "mil reais", "validade_dias": "sessenta", "validade_dias_num": 60, "prazo_execucao": "12 meses", "observacoes": "",
        "titulo": "DECLARAÇÃO", "texto": "Texto da declaração",
        "resumo_executivo": "r", "secoes": [{"chave": "a", "titulo": "A", "texto": "t"}], "premissas": ["p"],
        "margem_pct": "20", "impostos_pct": "16", "valor_hora_fmt": "160,00",
        "documentos": [{"ordem": 1, "titulo": "Proposta", "fase": "proposta", "arquivo": "01.pdf"}],
    }
    for p in tpls:
        t = DocxTemplate(str(p))
        t.render(ctx, autoescape=True)
        out = tmp_path / f"out_{p.name}"
        t.save(str(out))
        texto = "\n".join(par.text for par in Document(str(out)).paragraphs)
        for tb in Document(str(out)).tables:
            for row in tb.rows:
                texto += "\n" + " | ".join(c.text for c in row.cells)
        assert "{{" not in texto and "{%" not in texto, p.name
        assert "ACME LTDA" in texto


def test_declaracoes_formatam():
    campos = dict(razao_social="A", cnpj="1", endereco="e", representante="r", cpf="c", cargo="g", orgao="o", numero_edital="n", objeto="ob", cidade="ci", data_extenso="d", porte_extenso="ME")
    for k, d in DECLARACOES.items():
        assert d["texto"].format(**campos), k

from licitabot.db.models import Item
from licitabot.pipeline.pricing import precificar_item


def test_desenvolvimento_horas():
    it = Item(oportunidade_id=1, numero_item=1, descricao="Desenvolvimento de sistema", quantidade=1, unidade="Serviço", valor_unit_estimado=100000, valor_total_estimado=100000, tipo_precificacao="desenvolvimento", horas_estimadas=300)
    r = precificar_item(it, None, True)
    assert r.participar
    assert 70000 <= r.valor_total <= 100000  # entre o piso (70%) e o teto (estimado)
    assert abs(r.valor_total - 90000) < 1  # custo baixo -> sobe ao preço justo (90% do estimado)


def test_acima_do_estimado_nao_participa():
    it = Item(oportunidade_id=1, numero_item=1, descricao="Desenvolvimento", quantidade=1, unidade="Serviço", valor_unit_estimado=1000, valor_total_estimado=1000, tipo_precificacao="desenvolvimento", horas_estimadas=500)
    r = precificar_item(it, None, True)
    assert not r.participar
    assert "inexequível" in r.justificativa


def test_saas_usuarios_meses():
    it = Item(oportunidade_id=1, numero_item=2, descricao="Licença de uso de software para 100 usuários", quantidade=12, unidade="Mês", valor_unit_estimado=9000, valor_total_estimado=108000, tipo_precificacao="saas")
    r = precificar_item(it, None, True)
    assert r.participar
    assert r.valor_unitario > 0
    assert abs(r.valor_total - r.valor_unitario * 12) < 0.05


def test_ponto_de_funcao():
    it = Item(oportunidade_id=1, numero_item=5, descricao="Sustentação de software", quantidade=2000, unidade="PONTO DE FUNÇÃO", valor_unit_estimado=1279.45, valor_total_estimado=2558900, tipo_precificacao="manutencao")
    r = precificar_item(it, None, True)
    assert r.participar
    assert "PF" in r.justificativa
    # custo 2000 x 750 = 1,5M -> base ~2,14M < alvo 2,30M -> sobe ao preço justo (90% do estimado)
    assert abs(r.valor_total - 2_558_900 * 0.9) < 50  # tolerância: unitário arredondado a centavos x 2000


def test_exclusivo_me_epp_bloqueia():
    it = Item(oportunidade_id=1, numero_item=3, descricao="x", quantidade=1, unidade="un", valor_unit_estimado=10, tipo_beneficio="Participação exclusiva para ME/EPP", tipo_precificacao="desenvolvimento")
    r = precificar_item(it, None, False)
    assert not r.participar


def test_piso_aplicado():
    it = Item(oportunidade_id=1, numero_item=4, descricao="Manutenção", quantidade=1, unidade="Serviço", valor_unit_estimado=1_000_000, valor_total_estimado=1_000_000, tipo_precificacao="manutencao", horas_estimadas=10)
    r = precificar_item(it, None, True)
    assert r.participar
    assert abs(r.valor_total - 900_000) < 1  # preço justo (alvo) prevalece sobre o custo irrisório

"""Textos padrão das declarações exigidas em licitações (Lei 14.133/2021, LC 123/2006, minutas AGU).

Placeholders preenchidos pelo docgen: {razao_social}, {cnpj}, {endereco}, {representante}, {cpf}, {cargo},
{orgao}, {numero_edital}, {objeto}, {cidade}, {data_extenso}, {porte_extenso}.
"""

from __future__ import annotations

DECLARACOES: dict[str, dict[str, str]] = {
    "decl_habilitacao": {
        "titulo": "DECLARAÇÃO DE CUMPRIMENTO DOS REQUISITOS DE HABILITAÇÃO",
        "texto": (
            "{razao_social}, inscrita no CNPJ sob o nº {cnpj}, com sede em {endereco}, por intermédio de seu representante legal, "
            "{representante}, CPF nº {cpf}, DECLARA, sob as penas da lei, para fins de participação no {numero_edital}, promovido por {orgao}, "
            "que cumpre plenamente os requisitos de habilitação definidos no instrumento convocatório, nos termos do art. 63, inciso I, "
            "da Lei nº 14.133, de 1º de abril de 2021, e que sua proposta está em conformidade com as exigências do edital, "
            "ciente de que a declaração falsa sujeita a licitante às sanções previstas no art. 155 da referida Lei."
        ),
    },
    "decl_me_epp": {
        "titulo": "DECLARAÇÃO DE ENQUADRAMENTO COMO MICROEMPRESA OU EMPRESA DE PEQUENO PORTE",
        "texto": (
            "{razao_social}, inscrita no CNPJ sob o nº {cnpj}, por intermédio de seu representante legal, {representante}, CPF nº {cpf}, "
            "DECLARA, sob as penas da lei, que se enquadra na condição de {porte_extenso}, nos termos do art. 3º da Lei Complementar nº 123, "
            "de 14 de dezembro de 2006, e que não está incursa em nenhuma das vedações previstas no § 4º do mesmo artigo, "
            "fazendo jus ao tratamento diferenciado e favorecido previsto nos arts. 42 a 49 da referida Lei Complementar e no art. 4º da Lei nº 14.133/2021."
        ),
    },
    "decl_menor": {
        "titulo": "DECLARAÇÃO DE QUE NÃO EMPREGA MENOR",
        "texto": (
            "{razao_social}, inscrita no CNPJ sob o nº {cnpj}, por intermédio de seu representante legal, {representante}, CPF nº {cpf}, "
            "DECLARA, para fins do disposto no inciso XXXIII do art. 7º da Constituição Federal e no art. 68, inciso VI, da Lei nº 14.133/2021, "
            "que não emprega menor de dezoito anos em trabalho noturno, perigoso ou insalubre e não emprega menor de dezesseis anos, "
            "salvo na condição de aprendiz, a partir de quatorze anos."
        ),
    },
    "decl_fato_impeditivo": {
        "titulo": "DECLARAÇÃO DE INEXISTÊNCIA DE FATO IMPEDITIVO",
        "texto": (
            "{razao_social}, inscrita no CNPJ sob o nº {cnpj}, por intermédio de seu representante legal, {representante}, CPF nº {cpf}, "
            "DECLARA, sob as penas da lei, que até a presente data inexistem fatos impeditivos para sua habilitação no {numero_edital}, "
            "que não está impedida de licitar ou contratar com a Administração Pública, não foi declarada inidônea e não se enquadra "
            "em nenhuma das hipóteses de impedimento previstas no art. 14 da Lei nº 14.133/2021, ciente da obrigatoriedade de declarar ocorrências posteriores."
        ),
    },
    "decl_elaboracao_independente": {
        "titulo": "DECLARAÇÃO DE ELABORAÇÃO INDEPENDENTE DE PROPOSTA",
        "texto": (
            "{representante}, CPF nº {cpf}, como representante devidamente constituído de {razao_social}, CNPJ nº {cnpj}, doravante denominada Licitante, "
            "para fins do {numero_edital}, DECLARA, sob as penas da lei, em especial o art. 337-F do Código Penal, que: (a) a proposta apresentada "
            "foi elaborada de maneira independente pela Licitante, e o conteúdo da proposta não foi, no todo ou em parte, direta ou indiretamente, "
            "informado, discutido ou recebido de qualquer outro participante potencial ou de fato da licitação, por qualquer meio ou por qualquer pessoa; "
            "(b) a intenção de apresentar a proposta não foi informada, discutida ou recebida de qualquer outro participante potencial ou de fato; "
            "(c) não tentou, por qualquer meio ou por qualquer pessoa, influir na decisão de qualquer outro participante quanto a participar ou não da licitação; "
            "(d) o conteúdo da proposta não será, no todo ou em parte, comunicado ou discutido com qualquer outro participante antes da adjudicação do objeto; "
            "(e) o conteúdo da proposta não foi, no todo ou em parte, informado, discutido ou recebido de qualquer integrante do órgão licitante antes da abertura oficial das propostas; "
            "e (f) está plenamente ciente do teor e da extensão desta declaração e detém plenos poderes e informações para firmá-la."
        ),
    },
    "decl_pcd": {
        "titulo": "DECLARAÇÃO DE RESERVA DE CARGOS PARA PESSOA COM DEFICIÊNCIA E REABILITADO DA PREVIDÊNCIA SOCIAL",
        "texto": (
            "{razao_social}, inscrita no CNPJ sob o nº {cnpj}, por intermédio de seu representante legal, {representante}, CPF nº {cpf}, "
            "DECLARA, sob as penas da lei, para fins do disposto no art. 63, inciso IV, da Lei nº 14.133/2021, que cumpre as exigências de reserva de cargos "
            "para pessoa com deficiência e para reabilitado da Previdência Social, previstas em lei e em outras normas específicas, "
            "ou que, em razão do número de empregados, não está sujeita a tal obrigação (art. 93 da Lei nº 8.213/1991)."
        ),
    },
    "decl_lgpd": {
        "titulo": "DECLARAÇÃO DE CONFORMIDADE COM A LEI GERAL DE PROTEÇÃO DE DADOS",
        "texto": (
            "{razao_social}, inscrita no CNPJ sob o nº {cnpj}, por intermédio de seu representante legal, {representante}, CPF nº {cpf}, "
            "DECLARA que observa e observará, na execução do objeto do {numero_edital}, as disposições da Lei nº 13.709/2018 (Lei Geral de Proteção de Dados Pessoais), "
            "adotando medidas técnicas e administrativas aptas a proteger os dados pessoais tratados, limitando o tratamento à finalidade contratual "
            "e garantindo o sigilo, a integridade e a disponibilidade das informações."
        ),
    },
    "decl_ciencia_edital": {
        "titulo": "DECLARAÇÃO DE CIÊNCIA E CONCORDÂNCIA COM AS CONDIÇÕES DO EDITAL",
        "texto": (
            "{razao_social}, inscrita no CNPJ sob o nº {cnpj}, por intermédio de seu representante legal, {representante}, CPF nº {cpf}, "
            "DECLARA que tomou conhecimento de todas as informações e condições do {numero_edital} e de seus anexos, inclusive do Termo de Referência, "
            "para o cumprimento das obrigações objeto da licitação; que sua proposta contempla todos os custos diretos e indiretos, tributos, encargos "
            "e demais despesas necessárias à execução do objeto; e que está ciente das sanções previstas no art. 156 da Lei nº 14.133/2021."
        ),
    },
    "decl_nepotismo": {
        "titulo": "DECLARAÇÃO DE INEXISTÊNCIA DE VÍNCULO (NEPOTISMO)",
        "texto": (
            "{razao_social}, inscrita no CNPJ sob o nº {cnpj}, por intermédio de seu representante legal, {representante}, CPF nº {cpf}, "
            "DECLARA, sob as penas da lei, que não possui em seu quadro societário ou de empregados servidor ou dirigente de {orgao}, "
            "nem cônjuge, companheiro ou parente em linha reta, colateral ou por afinidade, até o terceiro grau, de agente público que exerça "
            "cargo em comissão ou função de confiança no órgão contratante, nos termos do art. 14 da Lei nº 14.133/2021 e da Súmula Vinculante nº 13 do STF."
        ),
    },
    "decl_trabalho_degradante": {
        "titulo": "DECLARAÇÃO DE NÃO UTILIZAÇÃO DE TRABALHO DEGRADANTE OU FORÇADO",
        "texto": (
            "{razao_social}, inscrita no CNPJ sob o nº {cnpj}, por intermédio de seu representante legal, {representante}, CPF nº {cpf}, "
            "DECLARA, sob as penas da lei, que não utiliza e não utilizará, na execução do objeto, trabalho degradante, forçado ou análogo ao de escravo, "
            "nos termos dos incisos III e IV do art. 1º e do inciso III do art. 5º da Constituição Federal, e que não possui condenação por tais práticas."
        ),
    },
}

# Conjunto padrão incluído em toda proposta (além dos que o edital exigir explicitamente)
PADRAO_SEMPRE = ["decl_habilitacao", "decl_menor", "decl_fato_impeditivo", "decl_elaboracao_independente", "decl_pcd", "decl_ciencia_edital"]

PORTE_EXTENSO = {"ME": "MICROEMPRESA", "EPP": "EMPRESA DE PEQUENO PORTE"}

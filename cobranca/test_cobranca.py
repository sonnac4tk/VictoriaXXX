from datetime import date
from decimal import Decimal

from cobranca import Config, executar, ler_mes, ler_valor, montar_dados, reais, tipo_do_dia

CFG = Config(dia_extrato=10, intervalo_lembrete=5, smtp_usuario="luz@gmail.com",
             nome_predio="Ed. Victoria", chave_pix="chave@pix",
             email_responsavel="eu@x.com")

MORADORES = [
    ["Apto", "Nome", "Email"],
    ["101", "Eu (responsável)", ""],
    ["102", "Ana Souza", "ana@x.com"],
    ["201", "Bruno Lima", "bruno@x.com; carla@x.com"],
    ["202", "Caio", "caio@x.com"],
]
CONTAS = [
    ["Mês", "Valor"],
    ["2026-10", "R$ 35,60"],
    ["11/2026", "36,40"],
]
PAGAMENTOS = [
    ["Data", "Apto", "Valor"],
    ["01/10/2026", "102", "50,00"],   # pagou adiantado
    ["12/10/2026", "201", "8,90"],
    ["12/11/2026", "201", "9,10"],    # em dia
    ["12/10/2026", "202", "8,90"],    # deve novembro
    ["15/10/2026", "999", "10,00"],   # apto inexistente
]


def dados():
    return montar_dados(MORADORES, CONTAS, PAGAMENTOS)


def saldos():
    return {m.apto: m.saldo for m in dados().moradores}


def test_ler_valor_e_mes():
    assert ler_valor("R$ 1.234,56") == Decimal("1234.56")
    assert ler_valor("36.40") == Decimal("36.40")
    assert ler_valor("50") == Decimal("50.00")
    assert ler_valor("") is None
    assert ler_mes("2026-11") == (2026, 11) and ler_mes("nov/2026") == (2026, 11)
    assert reais(Decimal("1234.5")) == "R$ 1.234,50"


def test_saldo_com_credito_e_divida():
    s = saldos()
    # partes: 35,60/4 = 8,90 ; 36,40/4 = 9,10 ; total 18,00
    assert s == {"101": Decimal("-18.00"), "102": Decimal("32.00"),
                 "201": Decimal("0.00"), "202": Decimal("-9.10")}


def test_pagamento_de_apto_inexistente_vira_aviso():
    assert any("999" in a for a in dados().avisos)


def test_dias_de_envio():
    assert tipo_do_dia(date(2026, 11, 10), CFG) == "extrato"
    assert tipo_do_dia(date(2026, 11, 15), CFG) == "lembrete"
    assert tipo_do_dia(date(2026, 11, 20), CFG) == "lembrete"
    assert tipo_do_dia(date(2026, 11, 12), CFG) is None
    assert tipo_do_dia(date(2026, 11, 5), CFG) is None


def test_extrato_vai_para_todos_com_email():
    msgs = executar(dados(), date(2026, 11, 10), CFG)
    assert [m["To"] for m in msgs] == ["ana@x.com", "bruno@x.com, carla@x.com",
                                       "caio@x.com", "eu@x.com"]
    ana = msgs[0].get_content()
    assert "R$ 36,40" in ana and "R$ 9,10" in ana and "R$ 32,00 de crédito" in ana
    assert "chave@pix" not in ana  # quem tem crédito não recebe PIX
    caio = msgs[2].get_content()
    assert "devendo R$ 9,10" in caio and "chave@pix" in caio
    resumo = msgs[3].get_content()
    assert "Apto 202" in resumo and "devendo" in resumo and "999" in resumo
    assert "Apto 101 (Eu (responsável)): não cobrado" in resumo


def test_lembrete_so_para_quem_deve():
    msgs = executar(dados(), date(2026, 11, 15), CFG)
    assert [m["To"] for m in msgs] == ["caio@x.com", "eu@x.com"]


def test_dia_sem_envio():
    assert executar(dados(), date(2026, 11, 12), CFG) == []

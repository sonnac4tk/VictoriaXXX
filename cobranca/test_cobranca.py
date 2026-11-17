from datetime import date

from cobranca import Config, decidir_aviso, executar, ler_mes, parse_planilha, vencimento

CFG = Config(dia_vencimento=10, dias_antes=3, intervalo_atraso=5,
             smtp_usuario="cond@gmail.com", nome_predio="Ed. Victoria")

PLANILHA = [
    ["Apto", "Nome", "Email", "Valor", "2026-10", "11/2026"],
    ["101", "Ana Souza", "ana@x.com", "R$ 450,00", "pago", ""],
    ["102", "Bruno Lima", "bruno@x.com; carla@x.com", "R$ 450,00", "pago", "pago"],
    ["103", "Caio", "caio@x.com", "R$ 450,00", "", "não"],
    ["104", "Sem email", "", "R$ 450,00", "", ""],
]


def moradores():
    return {m.apto: m for m in parse_planilha(PLANILHA)}


def test_ler_mes_formatos():
    assert ler_mes("2026-11") == (2026, 11)
    assert ler_mes("11/2026") == (2026, 11)
    assert ler_mes("01/11/2026") == (2026, 11)
    assert ler_mes("nov/2026") == (2026, 11)
    assert ler_mes("'2026-11") == (2026, 11)
    assert ler_mes("Valor") is None
    assert ler_mes("13/2026") is None


def test_parse_ignora_sem_email_e_separa_emails():
    ms = moradores()
    assert set(ms) == {"101", "102", "103"}
    assert ms["102"].emails == ["bruno@x.com", "carla@x.com"]
    assert ms["103"].pagamentos == {(2026, 10): False, (2026, 11): False}


def test_vencimento_mes_curto():
    assert vencimento(2026, 2, 31) == date(2026, 2, 28)


def test_lembrete_e_vence_hoje():
    ana = moradores()["101"]
    assert decidir_aviso(ana, date(2026, 11, 7), CFG).tipo == "lembrete"
    assert decidir_aviso(ana, date(2026, 11, 8), CFG) is None
    assert decidir_aviso(ana, date(2026, 11, 10), CFG).tipo == "vence_hoje"


def test_em_dia_nao_recebe():
    bruno = moradores()["102"]
    for dia in range(1, 31):
        assert decidir_aviso(bruno, date(2026, 11, dia), CFG) is None


def test_atraso_a_cada_intervalo():
    ana = moradores()["101"]
    dias_com_aviso = [d for d in range(11, 31)
                      if decidir_aviso(ana, date(2026, 11, d), CFG)]
    assert dias_com_aviso == [15, 20, 25, 30]


def test_atraso_conta_desde_o_mes_mais_antigo():
    caio = moradores()["103"]
    aviso = decidir_aviso(caio, date(2026, 11, 9), CFG)  # 10/10 + 30 dias
    assert aviso.tipo == "atraso"
    assert aviso.meses_abertos == [(2026, 10), (2026, 11)]


def test_mes_atual_sem_coluna_conta_como_aberto():
    linhas = [["Apto", "Nome", "Email", "2026-11"], ["201", "Duda", "d@x.com", "pago"]]
    duda = parse_planilha(linhas)[0]
    assert decidir_aviso(duda, date(2026, 12, 7), CFG).tipo == "lembrete"


def test_executar_monta_emails_e_resumo():
    cfg = Config(**{**CFG.__dict__, "email_sindico": "sindico@x.com", "chave_pix": "12.345.678/0001-90"})
    msgs = executar(PLANILHA, date(2026, 11, 15), cfg)
    para = [m["To"] for m in msgs]
    assert para == ["ana@x.com", "sindico@x.com"]  # Caio: 36 dias, fora do intervalo
    corpo = msgs[0].get_content()
    assert "novembro/2026" in corpo and "12.345.678/0001-90" in corpo
    assert msgs[0]["Reply-To"] == "sindico@x.com"
    assert "Apto 101" in msgs[1].get_content()

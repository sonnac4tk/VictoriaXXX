"""Rateio e cobrança da conta de luz do condomínio, por e-mail.

Cada morador tem um saldo: tudo o que já pagou menos a sua parte de cada conta
de luz. Quem pagou adiantado (ex.: R$ 50) fica com crédito e vai abatendo.

A planilha do Google tem três abas:

  Moradores:   Apto | Nome | Email
  Contas:      Mês | Valor             (ex.: 2026-11 | 36,40)
  Pagamentos:  Data | Apto | Valor     (ex.: 05/11/2026 | 101 | 50,00)

Cada conta é dividida igualmente entre todos os moradores da aba Moradores
(inclusive quem não tem e-mail, como o próprio responsável pela conta).

O programa roda uma vez por dia e:
  * no DIA_EXTRATO de cada mês, manda a todos um extrato com a última conta,
    a sua parte e o seu saldo;
  * depois disso, a cada INTERVALO_LEMBRETE dias, lembra quem está devendo;
  * manda ao responsável (EMAIL_RESPONSAVEL) um resumo com o saldo de todos.
"""

from __future__ import annotations

import json
import os
import re
import smtplib
import ssl
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from email.message import EmailMessage
from zoneinfo import ZoneInfo

MESES_PT = [
    "janeiro", "fevereiro", "março", "abril", "maio", "junho",
    "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
]
CENTAVO = Decimal("0.01")


# --------------------------------------------------------------------------- #
# Configuração
# --------------------------------------------------------------------------- #
@dataclass
class Config:
    dia_extrato: int = 10
    intervalo_lembrete: int = 5
    nome_predio: str = "Condomínio"
    chave_pix: str = ""
    sugestao_credito: str = "50,00"
    email_responsavel: str = ""
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 465
    smtp_usuario: str = ""
    smtp_senha: str = ""
    remetente_nome: str = "Conta de luz do condomínio"
    dry_run: bool = False

    @classmethod
    def do_ambiente(cls) -> "Config":
        env = os.environ.get

        def inteiro(nome: str, padrao: int) -> int:
            valor = (env(nome) or "").strip()
            return int(valor) if valor else padrao

        return cls(
            dia_extrato=inteiro("DIA_EXTRATO", 10),
            intervalo_lembrete=inteiro("INTERVALO_LEMBRETE", 5),
            nome_predio=env("NOME_PREDIO") or "Condomínio",
            chave_pix=env("CHAVE_PIX") or "",
            sugestao_credito=env("SUGESTAO_CREDITO") or "50,00",
            email_responsavel=env("EMAIL_RESPONSAVEL") or "",
            smtp_host=env("SMTP_HOST") or "smtp.gmail.com",
            smtp_port=inteiro("SMTP_PORT", 465),
            smtp_usuario=env("SMTP_USUARIO") or "",
            smtp_senha=env("SMTP_SENHA") or "",
            remetente_nome=env("REMETENTE_NOME") or "Conta de luz do condomínio",
            dry_run=(env("DRY_RUN") or "").strip().lower() in {"1", "true", "sim", "yes"},
        )


# --------------------------------------------------------------------------- #
# Leitura da planilha
# --------------------------------------------------------------------------- #
def ler_valor(texto: str) -> Decimal | None:
    """Converte "R$ 1.234,56", "36,40" ou "36.40" em Decimal."""
    t = re.sub(r"[^\d,.\-]", "", texto)
    if not t:
        return None
    if "," in t:  # formato brasileiro: ponto é milhar, vírgula é decimal
        t = t.replace(".", "").replace(",", ".")
    try:
        return Decimal(t).quantize(CENTAVO)
    except InvalidOperation:
        return None


def ler_mes(texto: str) -> tuple[int, int] | None:
    """Aceita "2026-11", "11/2026", "01/11/2026", "2026-11-01" e "nov/2026"."""
    t = texto.strip().lower().lstrip("'")
    if m := re.fullmatch(r"(\d{4})-(\d{1,2})(?:-\d{1,2})?", t):
        ano, mes = int(m[1]), int(m[2])
    elif m := re.fullmatch(r"(?:\d{1,2}/)?(\d{1,2})/(\d{4})", t):
        ano, mes = int(m[2]), int(m[1])
    elif m := re.fullmatch(r"([a-zç]{3})[a-zç]*[/\-. ]+(\d{4})", t):
        abrevs = [n[:3] for n in MESES_PT]
        if m[1] not in abrevs:
            return None
        ano, mes = int(m[2]), abrevs.index(m[1]) + 1
    else:
        return None
    return (ano, mes) if 1 <= mes <= 12 else None


def _tabela(linhas: list[list[str]], aba: str, **colunas: tuple[str, ...]) -> list[dict[str, str]]:
    """Transforma as linhas de uma aba em dicts, achando as colunas pelo nome."""
    if not linhas:
        return []
    cab = [c.strip().lower() for c in linhas[0]]
    indices = {}
    for chave, nomes in colunas.items():
        achado = next((cab.index(n) for n in nomes if n in cab), None)
        if achado is None:
            raise ValueError(f"Aba {aba!r}: coluna {nomes[0]!r} não encontrada")
        indices[chave] = achado
    return [
        {k: (linha[i].strip() if i < len(linha) else "") for k, i in indices.items()}
        for linha in linhas[1:]
        if any(c.strip() for c in linha)
    ]


@dataclass
class Morador:
    apto: str
    nome: str
    emails: list[str]
    pago: Decimal = Decimal("0")
    devido: Decimal = Decimal("0")

    @property
    def saldo(self) -> Decimal:
        return self.pago - self.devido


@dataclass
class Dados:
    moradores: list[Morador]
    contas: list[tuple[tuple[int, int], Decimal]]  # ((ano, mês), valor), em ordem
    avisos: list[str] = field(default_factory=list)  # linhas ignoradas etc.

    def parte(self, valor: Decimal) -> Decimal:
        return (valor / len(self.moradores)).quantize(CENTAVO, ROUND_HALF_UP)


def montar_dados(moradores: list[list[str]], contas: list[list[str]],
                 pagamentos: list[list[str]]) -> Dados:
    lista = [
        Morador(m["apto"], m["nome"],
                [e for e in re.split(r"[;,\s]+", m["email"]) if "@" in e])
        for m in _tabela(moradores, "Moradores", apto=("apto", "apartamento", "unidade"),
                         nome=("nome", "morador"), email=("email", "e-mail"))
        if m["apto"]
    ]
    if not lista:
        raise ValueError("A aba Moradores está vazia")
    dados = Dados(lista, [])
    por_apto = {m.apto.lower(): m for m in lista}

    for c in _tabela(contas, "Contas", mes=("mês", "mes", "referência", "referencia"),
                     valor=("valor",)):
        mes, valor = ler_mes(c["mes"]), ler_valor(c["valor"])
        if mes is None or valor is None:
            dados.avisos.append(f"Conta ignorada (mês ou valor inválido): {c}")
            continue
        dados.contas.append((mes, valor))
    dados.contas.sort()

    for mes, valor in dados.contas:
        for m in lista:
            m.devido += dados.parte(valor)

    for p in _tabela(pagamentos, "Pagamentos", apto=("apto", "apartamento", "unidade"),
                     valor=("valor",)):
        valor, morador = ler_valor(p["valor"]), por_apto.get(p["apto"].lower())
        if valor is None or morador is None:
            dados.avisos.append(f"Pagamento ignorado (apto ou valor inválido): {p}")
            continue
        morador.pago += valor
    return dados


def baixar_planilha() -> tuple[list[list[str]], list[list[str]], list[list[str]]]:
    import gspread  # importado aqui para os testes não dependerem dele

    cliente = gspread.service_account_from_dict(json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]))
    planilha = cliente.open_by_key(os.environ["PLANILHA_ID"])
    return tuple(planilha.worksheet(nome).get_all_values()
                 for nome in ("Moradores", "Contas", "Pagamentos"))


# --------------------------------------------------------------------------- #
# Regras e e-mails
# --------------------------------------------------------------------------- #
def reais(v: Decimal) -> str:
    texto = f"{abs(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {texto}"


def nome_mes(mes: tuple[int, int]) -> str:
    return f"{MESES_PT[mes[1] - 1]}/{mes[0]}"


def tipo_do_dia(hoje: date, cfg: Config) -> str | None:
    """"extrato" no dia do extrato, "lembrete" nos dias de lembrete, senão None."""
    dia = min(cfg.dia_extrato, 28)
    if hoje.day == dia:
        return "extrato"
    atraso = hoje.day - dia
    if atraso > 0 and cfg.intervalo_lembrete > 0 and atraso % cfg.intervalo_lembrete == 0:
        return "lembrete"
    return None


def texto_saldo(m: Morador) -> str:
    if m.saldo > 0:
        return f"Você tem {reais(m.saldo)} de crédito. Não precisa pagar nada agora."
    if m.saldo == 0:
        return "Seu saldo está zerado. Não precisa pagar nada agora."
    return f"Você está devendo {reais(-m.saldo)}."


def montar_email(m: Morador, tipo: str, dados: Dados, cfg: Config) -> tuple[str, str]:
    nome = m.nome.split()[0] if m.nome else "vizinho(a)"
    linhas = [f"Olá, {nome}!", ""]

    if tipo == "extrato":
        assunto = f"Luz do {cfg.nome_predio}: "
        if dados.contas:
            mes, valor = dados.contas[-1]
            assunto += nome_mes(mes)
            linhas += [
                f"A conta de luz de {nome_mes(mes)} veio {reais(valor)}.",
                f"Dividida por {len(dados.moradores)}, sua parte é {reais(dados.parte(valor))}.",
                "",
            ]
        else:
            assunto += "seu saldo"
        linhas += [f"Total que você já pagou: {reais(m.pago)}",
                   f"Total das suas partes: {reais(m.devido)}", "", texto_saldo(m)]
    else:
        assunto = f"Lembrete: luz do {cfg.nome_predio} em aberto"
        linhas += [f"Lembrete: ainda consta um valor em aberto da conta de luz. {texto_saldo(m)}"]

    if m.saldo < 0:
        if cfg.chave_pix:
            linhas += ["", f"Chave PIX: {cfg.chave_pix}"]
        if cfg.sugestao_credito:
            linhas += [f"Se preferir, pode mandar um valor redondo (ex.: R$ {cfg.sugestao_credito}): "
                       "o que sobrar fica de crédito para os próximos meses."]
        linhas += ["", "Se já pagou, desconsidere: a planilha pode ainda não ter sido atualizada."]
    linhas += ["", "Obrigado!", cfg.remetente_nome]
    return assunto, "\n".join(linhas)


def criar_mensagem(cfg: Config, para: list[str], assunto: str, corpo: str) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = f"{cfg.remetente_nome} <{cfg.smtp_usuario}>"
    msg["To"] = ", ".join(para)
    msg["Subject"] = assunto
    if cfg.email_responsavel:
        msg["Reply-To"] = cfg.email_responsavel
    msg.set_content(corpo)
    return msg


def executar(dados: Dados, hoje: date, cfg: Config) -> list[EmailMessage]:
    tipo = tipo_do_dia(hoje, cfg)
    if tipo is None:
        return []
    alvos = [m for m in dados.moradores if m.emails and (tipo == "extrato" or m.saldo < 0)]
    mensagens = [criar_mensagem(cfg, m.emails, *montar_email(m, tipo, dados, cfg)) for m in alvos]

    if cfg.email_responsavel and (tipo == "extrato" or alvos):
        resumo = [f"Apto {m.apto} ({m.nome}): "
                  + (f"saldo {'-' if m.saldo < 0 else ''}{reais(m.saldo)}"
                     + (" ← devendo" if m.saldo < 0 else "") if m.emails
                     else "não cobrado (sem e-mail)")
                  for m in dados.moradores]
        enviados = ", ".join(m.apto for m in alvos) or "ninguém"
        corpo = "\n".join(["Saldos de hoje:", "", *resumo, "", f"E-mails enviados para: {enviados}"]
                          + (["", "Atenção:", *dados.avisos] if dados.avisos else []))
        mensagens.append(criar_mensagem(
            cfg, [cfg.email_responsavel],
            f"Resumo luz {hoje:%d/%m/%Y} ({'extrato' if tipo == 'extrato' else 'lembretes'})", corpo))
    return mensagens


def enviar(cfg: Config, mensagens: list[EmailMessage]) -> None:
    if cfg.dry_run:
        for msg in mensagens:
            print(f"\n[SIMULAÇÃO] Para: {msg['To']}\nAssunto: {msg['Subject']}\n{msg.get_content()}")
        return
    if not mensagens:
        return
    contexto = ssl.create_default_context()
    if cfg.smtp_port == 465:
        servidor = smtplib.SMTP_SSL(cfg.smtp_host, cfg.smtp_port, context=contexto)
    else:
        servidor = smtplib.SMTP(cfg.smtp_host, cfg.smtp_port)
        servidor.starttls(context=contexto)
    with servidor:
        servidor.login(cfg.smtp_usuario, cfg.smtp_senha)
        for msg in mensagens:
            servidor.send_message(msg)
            print(f"Enviado: {msg['To']} — {msg['Subject']}")


def main() -> int:
    cfg = Config.do_ambiente()
    hoje = datetime.now(ZoneInfo("America/Sao_Paulo")).date()
    if data := os.environ.get("DATA_HOJE"):  # para testar um dia específico
        hoje = date.fromisoformat(data)
    if not cfg.dry_run and not (cfg.smtp_usuario and cfg.smtp_senha):
        print("Faltam SMTP_USUARIO e/ou SMTP_SENHA.", file=sys.stderr)
        return 1

    dados = montar_dados(*baixar_planilha())
    for aviso in dados.avisos:
        print("Aviso:", aviso)
    mensagens = executar(dados, hoje, cfg)
    print(f"{hoje:%d/%m/%Y}: {len(mensagens)} e-mail(s) a enviar.")
    enviar(cfg, mensagens)
    return 0


if __name__ == "__main__":
    sys.exit(main())

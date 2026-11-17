"""Cobrança automática da mensalidade do condomínio por e-mail.

Lê uma planilha do Google com os moradores e os meses pagos, e envia e-mail
para quem está com a mensalidade pendente. Feito para rodar uma vez por dia
(ex.: GitHub Actions); ele mesmo decide se hoje é dia de mandar cada aviso:

  * lembrete DIAS_ANTES dias antes do vencimento;
  * aviso no dia do vencimento;
  * aviso de atraso a cada INTERVALO_ATRASO dias enquanto houver mês em aberto.

Formato da planilha (primeira linha = cabeçalho):

  Apto | Nome  | Email          | Valor     | 2026-10 | 2026-11 | 2026-12 ...
  101  | Ana   | ana@email.com  | R$ 450,00 | pago    |         |
  102  | Bruno | b@x.com; c@y.com | R$ 450,00 | pago  | pago    |

Célula do mês preenchida = pago. Vazia (ou "não", "pendente") = em aberto.
"""

from __future__ import annotations

import calendar
import json
import os
import re
import smtplib
import ssl
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from email.message import EmailMessage
from zoneinfo import ZoneInfo

MESES_PT = [
    "janeiro", "fevereiro", "março", "abril", "maio", "junho",
    "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
]
NAO_PAGO = {"", "não", "nao", "n", "pendente", "aberto", "em aberto", "-"}


# --------------------------------------------------------------------------- #
# Configuração
# --------------------------------------------------------------------------- #
@dataclass
class Config:
    dia_vencimento: int = 10
    dias_antes: int = 3
    intervalo_atraso: int = 5
    nome_predio: str = "Condomínio"
    chave_pix: str = ""
    instrucoes_extra: str = ""
    email_sindico: str = ""
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 465
    smtp_usuario: str = ""
    smtp_senha: str = ""
    remetente_nome: str = "Administração do Condomínio"
    dry_run: bool = False

    @classmethod
    def do_ambiente(cls) -> "Config":
        env = os.environ.get

        def inteiro(nome: str, padrao: int) -> int:
            valor = (env(nome) or "").strip()
            return int(valor) if valor else padrao

        return cls(
            dia_vencimento=inteiro("DIA_VENCIMENTO", 10),
            dias_antes=inteiro("DIAS_ANTES", 3),
            intervalo_atraso=inteiro("INTERVALO_ATRASO", 5),
            nome_predio=env("NOME_PREDIO") or "Condomínio",
            chave_pix=env("CHAVE_PIX") or "",
            instrucoes_extra=env("INSTRUCOES_EXTRA") or "",
            email_sindico=env("EMAIL_SINDICO") or "",
            smtp_host=env("SMTP_HOST") or "smtp.gmail.com",
            smtp_port=inteiro("SMTP_PORT", 465),
            smtp_usuario=env("SMTP_USUARIO") or "",
            smtp_senha=env("SMTP_SENHA") or "",
            remetente_nome=env("REMETENTE_NOME") or "Administração do Condomínio",
            dry_run=(env("DRY_RUN") or "").strip().lower() in {"1", "true", "sim", "yes"},
        )


# --------------------------------------------------------------------------- #
# Planilha
# --------------------------------------------------------------------------- #
@dataclass
class Morador:
    apto: str
    nome: str
    emails: list[str]
    valor: str
    pagamentos: dict[tuple[int, int], bool] = field(default_factory=dict)


def ler_mes(cabecalho: str) -> tuple[int, int] | None:
    """Converte o cabeçalho de uma coluna em (ano, mês), ou None se não for mês.

    Aceita "2026-11", "11/2026", "01/11/2026", "2026-11-01" e "nov/2026".
    """
    texto = cabecalho.strip().lower().lstrip("'")
    m = re.fullmatch(r"(\d{4})-(\d{1,2})(?:-\d{1,2})?", texto)
    if m:
        ano, mes = int(m[1]), int(m[2])
    elif m := re.fullmatch(r"(?:\d{1,2}/)?(\d{1,2})/(\d{4})", texto):
        ano, mes = int(m[2]), int(m[1])
    elif m := re.fullmatch(r"([a-zç]{3})[a-zç]*[/\-. ]+(\d{4})", texto):
        abrevs = [n[:3] for n in MESES_PT]
        if m[1] not in abrevs:
            return None
        ano, mes = int(m[2]), abrevs.index(m[1]) + 1
    else:
        return None
    return (ano, mes) if 1 <= mes <= 12 else None


def _coluna(cabecalhos: list[str], *nomes: str) -> int:
    normal = [c.strip().lower() for c in cabecalhos]
    for nome in nomes:
        if nome in normal:
            return normal.index(nome)
    raise ValueError(f"Coluna obrigatória não encontrada na planilha: {nomes[0]!r}")


def parse_planilha(linhas: list[list[str]]) -> list[Morador]:
    if not linhas:
        return []
    cab = linhas[0]
    i_apto = _coluna(cab, "apto", "apartamento", "unidade")
    i_nome = _coluna(cab, "nome", "morador")
    i_email = _coluna(cab, "email", "e-mail")
    try:
        i_valor = _coluna(cab, "valor")
    except ValueError:
        i_valor = None
    meses = {i: m for i, c in enumerate(cab) if (m := ler_mes(c))}

    moradores = []
    for linha in linhas[1:]:
        celula = lambda i: linha[i].strip() if i is not None and i < len(linha) else ""
        emails = [e.strip() for e in re.split(r"[;,\s]+", celula(i_email)) if "@" in e]
        if not emails:
            continue
        pagamentos = {m: celula(i).lower() not in NAO_PAGO for i, m in meses.items()}
        moradores.append(Morador(celula(i_apto), celula(i_nome), emails, celula(i_valor), pagamentos))
    return moradores


def baixar_planilha() -> list[list[str]]:
    import gspread  # importado aqui para os testes não dependerem dele

    credenciais = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
    cliente = gspread.service_account_from_dict(credenciais)
    planilha = cliente.open_by_key(os.environ["PLANILHA_ID"])
    aba_nome = os.environ.get("PLANILHA_ABA")
    aba = planilha.worksheet(aba_nome) if aba_nome else planilha.sheet1
    return aba.get_all_values()


# --------------------------------------------------------------------------- #
# Regras de cobrança
# --------------------------------------------------------------------------- #
def vencimento(ano: int, mes: int, dia: int) -> date:
    """Dia de vencimento do mês; se o mês for curto, usa o último dia."""
    return date(ano, mes, min(dia, calendar.monthrange(ano, mes)[1]))


@dataclass
class Aviso:
    tipo: str  # "lembrete" | "vence_hoje" | "atraso"
    morador: Morador
    meses_abertos: list[tuple[int, int]]


def decidir_aviso(m: Morador, hoje: date, cfg: Config) -> Aviso | None:
    atual = (hoje.year, hoje.month)
    meses = set(m.pagamentos) | {atual}
    abertos = sorted(x for x in meses if x <= atual and not m.pagamentos.get(x, False))
    if not abertos:
        return None

    venc_atual = vencimento(*atual, cfg.dia_vencimento)
    atrasados = [x for x in abertos if vencimento(*x, cfg.dia_vencimento) < hoje]

    if atrasados:
        dias = (hoje - vencimento(*atrasados[0], cfg.dia_vencimento)).days
        if cfg.intervalo_atraso > 0 and dias % cfg.intervalo_atraso == 0:
            return Aviso("atraso", m, abertos)
        return None
    if hoje == venc_atual:
        return Aviso("vence_hoje", m, abertos)
    if cfg.dias_antes > 0 and (venc_atual - hoje).days == cfg.dias_antes:
        return Aviso("lembrete", m, abertos)
    return None


# --------------------------------------------------------------------------- #
# E-mail
# --------------------------------------------------------------------------- #
def nome_mes(ano: int, mes: int) -> str:
    return f"{MESES_PT[mes - 1]}/{ano}"


def montar_email(aviso: Aviso, hoje: date, cfg: Config) -> tuple[str, str]:
    m = aviso.morador
    primeiro_nome = m.nome.split()[0] if m.nome else "morador(a)"
    lista = ", ".join(nome_mes(*x) for x in aviso.meses_abertos)
    venc = vencimento(hoje.year, hoje.month, cfg.dia_vencimento).strftime("%d/%m/%Y")
    valor = f" no valor de {m.valor}" if m.valor else ""
    apto = f" — Apto {m.apto}" if m.apto else ""

    if aviso.tipo == "lembrete":
        assunto = f"Lembrete: condomínio vence em {venc}{apto}"
        corpo = (f"Olá, {primeiro_nome}!\n\n"
                 f"Passando para lembrar que a mensalidade do {cfg.nome_predio}{valor} "
                 f"vence em {venc}.")
    elif aviso.tipo == "vence_hoje":
        assunto = f"Condomínio vence hoje ({venc}){apto}"
        corpo = (f"Olá, {primeiro_nome}!\n\n"
                 f"A mensalidade do {cfg.nome_predio}{valor} vence hoje, {venc}.")
    else:
        assunto = f"Mensalidade do condomínio em atraso{apto}"
        plural = "s" if len(aviso.meses_abertos) > 1 else ""
        corpo = (f"Olá, {primeiro_nome}!\n\n"
                 f"Não identificamos o pagamento da{plural} mensalidade{plural} do "
                 f"{cfg.nome_predio} referente a: {lista}.\n"
                 f"Pedimos que regularize o quanto antes.")

    if cfg.chave_pix:
        corpo += f"\n\nChave PIX para pagamento: {cfg.chave_pix}"
    if cfg.instrucoes_extra:
        corpo += f"\n\n{cfg.instrucoes_extra}"
    corpo += ("\n\nSe você já pagou, por favor desconsidere esta mensagem "
              "(ou envie o comprovante para a administração).\n\n"
              f"Atenciosamente,\n{cfg.remetente_nome}")
    return assunto, corpo


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


def criar_mensagem(cfg: Config, para: list[str], assunto: str, corpo: str) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = f"{cfg.remetente_nome} <{cfg.smtp_usuario}>"
    msg["To"] = ", ".join(para)
    msg["Subject"] = assunto
    if cfg.email_sindico:
        msg["Reply-To"] = cfg.email_sindico
    msg.set_content(corpo)
    return msg


# --------------------------------------------------------------------------- #
# Principal
# --------------------------------------------------------------------------- #
def executar(linhas: list[list[str]], hoje: date, cfg: Config) -> list[EmailMessage]:
    avisos = [a for m in parse_planilha(linhas) if (a := decidir_aviso(m, hoje, cfg))]
    mensagens = [criar_mensagem(cfg, a.morador.emails, *montar_email(a, hoje, cfg)) for a in avisos]

    if cfg.email_sindico and avisos:
        rotulo = {"lembrete": "lembrete", "vence_hoje": "vence hoje", "atraso": "atraso"}
        resumo = "\n".join(
            f"- Apto {a.morador.apto} ({a.morador.nome}): {rotulo[a.tipo]} — "
            + ", ".join(nome_mes(*x) for x in a.meses_abertos)
            for a in avisos
        )
        mensagens.append(criar_mensagem(
            cfg, [cfg.email_sindico],
            f"Resumo da cobrança {hoje.strftime('%d/%m/%Y')} — {len(avisos)} aviso(s)",
            f"Avisos enviados hoje:\n\n{resumo}\n",
        ))
    return mensagens


def main() -> int:
    cfg = Config.do_ambiente()
    hoje = datetime.now(ZoneInfo("America/Sao_Paulo")).date()
    if data := os.environ.get("DATA_HOJE"):  # para testar um dia específico
        hoje = date.fromisoformat(data)
    if not cfg.dry_run and not (cfg.smtp_usuario and cfg.smtp_senha):
        print("Faltam SMTP_USUARIO e/ou SMTP_SENHA.", file=sys.stderr)
        return 1

    mensagens = executar(baixar_planilha(), hoje, cfg)
    print(f"{hoje:%d/%m/%Y}: {len(mensagens)} e-mail(s) a enviar.")
    enviar(cfg, mensagens)
    return 0


if __name__ == "__main__":
    sys.exit(main())

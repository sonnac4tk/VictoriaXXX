# Cobrança automática do condomínio

Todo dia, na nuvem (GitHub Actions, grátis), o programa lê a planilha do Google
e manda e-mail **só para quem não pagou**:

| Quando | Aviso |
|---|---|
| 3 dias antes do vencimento | Lembrete |
| No dia do vencimento | "Vence hoje" |
| Depois do vencimento, a cada 5 dias | Aviso de atraso (lista todos os meses em aberto) |

Os números são configuráveis. Se você cadastrar o e-mail do síndico, ele recebe
um resumo de quem foi cobrado em cada dia, e as respostas dos moradores vão para ele.

## 1. Montar a planilha

Primeira linha = cabeçalho. Uma linha por apartamento, uma coluna por mês:

| Apto | Nome | Email | Valor | 2026-10 | 2026-11 | 2026-12 |
|---|---|---|---|---|---|---|
| 101 | Ana Souza | ana@email.com | R$ 450,00 | pago | | |
| 102 | Bruno Lima | bruno@email.com; carla@email.com | R$ 450,00 | pago | pago | |

- Quando alguém pagar, escreva qualquer coisa na célula do mês (`pago`, `x`, a data…).
  Célula vazia, `não` ou `pendente` = em aberto.
- Mais de um e-mail por apartamento: separe com `;`.
- O mês pode ser escrito como `2026-11`, `11/2026` ou `nov/2026`. Se o Google
  transformar o cabeçalho em data, comece com apóstrofo: `'2026-11`.
- O mês atual é cobrado mesmo que você ainda não tenha criado a coluna dele.

## 2. Dar acesso à planilha (conta de serviço do Google)

1. Acesse https://console.cloud.google.com, crie um projeto e ative a **Google Sheets API**.
2. Em *IAM e administrador → Contas de serviço*, crie uma conta de serviço.
   Na aba *Chaves*, crie uma chave **JSON** e baixe o arquivo.
3. Abra a planilha, clique em **Compartilhar** e compartilhe com o e-mail da conta
   de serviço (algo como `cobranca@seu-projeto.iam.gserviceaccount.com`) como **Leitor**.
4. O ID da planilha é o trecho do link entre `/d/` e `/edit`.

## 3. Senha de e-mail (Gmail)

Use um Gmail do condomínio. Ative a verificação em duas etapas e crie uma
**senha de app** em https://myaccount.google.com/apppasswords. Use essa senha
de 16 letras (não a senha normal).

## 4. Configurar no GitHub

No repositório: **Settings → Secrets and variables → Actions**.

**Secrets** (ficam escondidos):

| Nome | Valor |
|---|---|
| `GOOGLE_SERVICE_ACCOUNT_JSON` | conteúdo inteiro do arquivo JSON baixado |
| `PLANILHA_ID` | ID da planilha |
| `SMTP_USUARIO` | e-mail que envia (ex.: `condominio@gmail.com`) |
| `SMTP_SENHA` | senha de app |
| `EMAIL_SINDICO` | (opcional) recebe o resumo diário e as respostas |
| `CHAVE_PIX` | (opcional) aparece no e-mail |

**Variables** (todas opcionais):

| Nome | Padrão | O que é |
|---|---|---|
| `DIA_VENCIMENTO` | `10` | dia do mês em que vence |
| `DIAS_ANTES` | `3` | lembrete quantos dias antes (0 = não manda) |
| `INTERVALO_ATRASO` | `5` | repetir aviso de atraso a cada N dias (0 = não manda) |
| `NOME_PREDIO` | `Condomínio` | nome que aparece no e-mail |
| `REMETENTE_NOME` | `Administração do Condomínio` | assinatura |
| `INSTRUCOES_EXTRA` | | texto extra (dados bancários, boleto, etc.) |
| `PLANILHA_ABA` | primeira aba | nome da aba da planilha |
| `SMTP_HOST` / `SMTP_PORT` | `smtp.gmail.com` / `465` | para outro provedor de e-mail |

## 5. Testar

Na aba **Actions → Cobrança do condomínio → Run workflow**. Com "Só simular"
marcado, nada é enviado: os e-mails aparecem no log. Dá para preencher uma data
(ex.: `2026-11-15`) para ver o que seria enviado naquele dia.

Depois disso ele roda sozinho todo dia. Observação: o GitHub pausa agendamentos
de repositórios sem nenhuma atividade por 60 dias; se acontecer, basta clicar em
"Enable workflow" na aba Actions.

## Rodar localmente / testes

```bash
cd cobranca
pip install -r requirements.txt pytest
python -m pytest
DRY_RUN=1 PLANILHA_ID=... GOOGLE_SERVICE_ACCOUNT_JSON="$(cat chave.json)" python cobranca.py
```

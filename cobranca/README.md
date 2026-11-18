# Rateio da conta de luz do condomínio

A conta de luz está no nome de um morador, que paga e depois cobra os outros.
Este programa faz a conta e a cobrança sozinho, todo dia, na nuvem
(GitHub Actions, grátis).

Cada morador tem um **saldo**: tudo o que já pagou menos a sua parte de cada
conta. Quem manda R$ 50 adiantado fica com crédito e vai abatendo mês a mês.
Quem fica negativo é cobrado.

| Quando | O que acontece |
|---|---|
| Dia 10 de cada mês | Todos recebem um extrato: valor da última conta, a sua parte e o seu saldo. Só quem deve recebe a chave PIX. |
| Depois, a cada 5 dias | Lembrete só para quem está devendo. |
| Nos mesmos dias | O responsável recebe um resumo com o saldo de todos. |

## 1. Planilha do Google (3 abas, com estes nomes)

**Moradores**

| Apto | Nome | Email |
|---|---|---|
| 101 | Você | |
| 102 | Ana | ana@email.com |
| 201 | Bruno | bruno@email.com; carla@email.com |
| 202 | Caio | caio@email.com |

A conta é dividida igualmente por **todas** as linhas. Deixe o seu e-mail em
branco: a sua parte entra na divisão, mas você não é cobrado.

**Contas**: uma linha por conta de luz que chegar

| Mês | Valor |
|---|---|
| 2026-10 | 35,60 |
| 2026-11 | 36,40 |

**Pagamentos**: uma linha a cada PIX recebido

| Data | Apto | Valor |
|---|---|---|
| 01/10/2026 | 102 | 50,00 |
| 12/10/2026 | 201 | 8,90 |

Para acertar o histórico de quem já tinha crédito, lance um pagamento com o
crédito atual da pessoa. O mês pode ser escrito `2026-11`, `11/2026` ou
`nov/2026`; se o Google transformar em data, comece com apóstrofo (`'2026-11`).

## 2. Dar acesso à planilha (conta de serviço do Google)

1. Em https://console.cloud.google.com crie um projeto e ative a **Google Sheets API**.
2. Em *IAM e administrador → Contas de serviço*, crie uma conta de serviço e,
   na aba *Chaves*, crie uma chave **JSON**.
3. Compartilhe a planilha com o e-mail da conta de serviço
   (`...@...iam.gserviceaccount.com`) como **Leitor**.
4. O ID da planilha é o trecho do link entre `/d/` e `/edit`.

## 3. Senha do Gmail que vai enviar

Ative a verificação em duas etapas e crie uma **senha de app** em
https://myaccount.google.com/apppasswords.

## 4. Configurar no GitHub

**Settings → Secrets and variables → Actions**

Secrets:

| Nome | Valor |
|---|---|
| `GOOGLE_SERVICE_ACCOUNT_JSON` | conteúdo do arquivo JSON |
| `PLANILHA_ID` | ID da planilha |
| `SMTP_USUARIO` | Gmail que envia |
| `SMTP_SENHA` | senha de app |
| `EMAIL_RESPONSAVEL` | seu e-mail: recebe o resumo e as respostas dos moradores |
| `CHAVE_PIX` | sua chave PIX |

Variables (opcionais):

| Nome | Padrão | O que é |
|---|---|---|
| `DIA_EXTRATO` | `10` | dia do mês do extrato |
| `INTERVALO_LEMBRETE` | `5` | lembrar quem deve a cada N dias (0 = nunca) |
| `SUGESTAO_CREDITO` | `50,00` | valor redondo sugerido para pagar adiantado (vazio = não sugere) |
| `NOME_PREDIO` | `Condomínio` | nome no assunto do e-mail |
| `REMETENTE_NOME` | `Conta de luz do condomínio` | assinatura |

## 5. Testar

**Actions → Cobrança da luz → Run workflow**, com "Só simular" marcado: nada é
enviado e os e-mails aparecem no log. Preencha uma data (ex.: `2026-11-10`)
para ver o que sairia naquele dia.

O GitHub só roda o agendamento a partir do branch principal (`main`) e o pausa
se o repositório ficar 60 dias sem atividade; nesse caso, reative na aba Actions.

## Testes locais

```bash
cd cobranca && pip install -r requirements.txt pytest && python -m pytest
```

# RecargaPay PIX API

API de automação de pagamentos PIX via RecargaPay, construída com **FastAPI** e pronta para deploy no **Railway**.

## Endpoints

| Método | Rota | Descrição |
|--------|------|-----------|
| `GET`  | `/` | Boas-vindas e mapa de endpoints |
| `GET`  | `/health` | Health check (Railway) |
| `GET`  | `/status` | Status de todos os tokens |
| `POST` | `/status/renew` | Renovar Bearer Token manualmente |
| `POST` | `/pix/key` | Pagar PIX por chave (CPF/CNPJ/PHONE/EMAIL/EVP) |
| `POST` | `/pix/contact` | Pagar PIX por contato recente |
| `GET`  | `/pix/history` | Histórico de transações |
| `GET`  | `/pix/contacts` | Listar contatos recentes |
| `GET`  | `/pix/keys` | Listar chaves PIX da conta |
| `GET`  | `/pix/balance` | Consultar saldo da carteira |
| `POST` | `/pix/lookup` | Consultar destinatário sem pagar |
| `GET`  | `/pix/integrity-hash` | Gerar integrityHash para uma chave |
| `GET`  | `/docs` | Swagger UI (documentação interativa) |
| `GET`  | `/redoc` | ReDoc |

## Deploy no Railway

1. Acesse [railway.app](https://railway.app) e crie um novo projeto
2. Selecione **"Deploy from GitHub repo"** e escolha este repositório
3. Em **Settings > Variables**, adicione as variáveis do `.env.example`
4. O Railway detecta automaticamente o `railway.toml` e faz o build

## Variáveis de Ambiente Obrigatórias

| Variável | Descrição |
|----------|-----------|
| `GOOGLE_REFRESH_TOKEN` | Token permanente Google OAuth2 (renovação automática do Bearer) |
| `BEARER_TOKEN` | Bearer Token RecargaPay atual |
| `PIN_CODE` | PIN de segurança da conta |

As demais variáveis já têm valores padrão extraídos do APK/HAR.

## Exemplo de uso

```bash
# Pagar R$ 10,00 por CPF
curl -X POST https://sua-api.railway.app/pix/key \
  -H "Content-Type: application/json" \
  -d '{"key_type": "CPF", "key_value": "55537568802", "amount": "10.00"}'

# Verificar status dos tokens
curl https://sua-api.railway.app/status
```

### Resposta do pagamento

Os endpoints `POST /pix/key` e `POST /pix/contact` retornam somente os dados úteis
da operação. O conteúdo interno do provedor, como banners e configurações de tela,
não é exposto.

```json
{
  "success": true,
  "message": "Pix realizado com sucesso.",
  "transaction": {
    "status": "completed",
    "amount": {
      "value": "0.01",
      "currency": "BRL",
      "formatted": "R$ 0,01"
    },
    "payment_method": "WALLET",
    "receipt_url": "https://recargapay.com.br/user/history/1234567890/voucher",
    "created_at": "2026-09-12T17:11:51.687948",
    "references": {
      "pix_id": "pix-exemplo",
      "cart_id": "cart-exemplo",
      "run_id": "run-exemplo",
      "order_id": 1234567890
    }
  },
  "receiver": {
    "name": "Cliente Exemplo",
    "document": "***.456.789-**",
    "key": "12345678909",
    "key_type": "CPF",
    "institution": {
      "name": "PICPAY",
      "ispb": "22896431",
      "branch": "***",
      "account_number": "*****"
    }
  }
}
```

### Resposta do saldo

As contas internas com saldo e bloqueio zerados são removidas automaticamente.

```json
{
  "success": true,
  "message": "Saldo consultado com sucesso.",
  "wallet_available": true,
  "balance": {
    "available": 0.09,
    "blocked": 0.0,
    "available_for_discounts": 0.09,
    "currency": "BRL",
    "formatted_available": "R$ 0,09",
    "formatted_blocked": "R$ 0,00"
  },
  "sources": [
    {
      "id": "user-cashin-pix",
      "amount": 0.09,
      "blocked": 0.0,
      "formatted_amount": "R$ 0,09",
      "tags": ["available"]
    }
  ]
}
```

### Resposta da consulta de chave

```json
{
  "success": true,
  "found": true,
  "message": "Chave Pix encontrada.",
  "pix_id": "pix-exemplo",
  "receiver": {
    "name": "Cliente Exemplo",
    "document": "***.456.789-**",
    "owner_type": "NATURAL_PERSON",
    "same_owner": false,
    "key": "12345678909",
    "key_type": "CPF",
    "institution": {
      "name": "BANCO EXEMPLO",
      "ispb": "12345678",
      "branch": "***",
      "account_number": "*****"
    }
  }
}
```

## Renovação Automática de Tokens

A API renova o Bearer Token automaticamente em 2 etapas:
1. **Google OAuth2**: `GOOGLE_REFRESH_TOKEN` → novo `access_token` (sem `client_secret`)
2. **RecargaPay login**: `grant_type=google_access_token` → novo Bearer Token (~24h)

Nenhuma intervenção manual é necessária enquanto o `GOOGLE_REFRESH_TOKEN` estiver válido.

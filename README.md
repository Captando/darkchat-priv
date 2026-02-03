# Private DarkChat

![CI](https://github.com/<owner>/darkchat-priv/actions/workflows/ci.yml/badge.svg)
![Python](https://img.shields.io/badge/python-3.11%2B-3776AB)
![FastAPI](https://img.shields.io/badge/FastAPI-0.0.0-009688)
![License](https://img.shields.io/badge/license-MIT-black)
![Repo](https://img.shields.io/badge/repo-darkchat--priv-black)

Aplicação de chat privado com FastAPI + WebSockets e frontend em HTML/Tailwind. Inclui perfis, álbuns privados com links temporários, mídia no chat (com preview), localização e criptografia ponta‑a‑ponta opcional no cliente.

## Recursos
- Login/senha com SQLite
- Perfis com avatar, banner, bio e galeria
- Descoberta por distância (geolocalização do navegador)
- Chat em tempo real via WebSocket
- Envio de mídia e localização no chat
- Álbuns privados com links temporários (1h/24h/7d) e revogação
- E2EE opcional no chat (AES‑GCM no cliente)

## Requisitos
- Python 3.11+
- Docker (opcional)

## Rodando local (venv)
```bash
cd /Users/victorpcsca/Documents/Chat
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload
```

Acesse: `http://127.0.0.1:8000`

## Rodando com Docker
```bash
docker compose up --build
```

## Deploy (exemplos)
### Docker em VPS
```bash
git clone Captando/darkchat-priv
cd darkchat-priv
docker compose up -d --build
```

### Render/Fly.io/Outro
- Use `Dockerfile` da raiz.
- Exponha a porta `8000`.
- Monte um volume persistente para `DATA_DIR` (veja abaixo).

## Variáveis de Ambiente
- `DATA_DIR`: diretório para o banco e uploads (padrão: `./data`).
- `DB_PATH`: caminho completo do SQLite (padrão: `${DATA_DIR}/app.db`).
- `UPLOAD_DIR`: caminho para uploads (padrão: `${DATA_DIR}/uploads`).

Exemplo:
```bash
export DATA_DIR=/data
export DB_PATH=/data/app.db
export UPLOAD_DIR=/data/uploads
```

## Estrutura
- `main.py` — backend FastAPI
- `templates/index.html` — frontend
- `data/` — banco SQLite e uploads (criado automaticamente)

## Observações
- Salas são efêmeras (memória) e desaparecem ao reiniciar o servidor.
- Links de álbuns expiram automaticamente e podem ser revogados.
- E2EE é opcional e depende do segredo definido no cliente.

## Segurança / E2EE
Este projeto oferece **criptografia ponta‑a‑ponta opcional** no chat. Quando ativada:
- **Texto, mídia e localização** são cifrados no cliente (AES‑GCM via WebCrypto).
- A chave é derivada de um **segredo local** informado pelo usuário.
- O servidor **não consegue** decifrar o conteúdo (apenas roteia payloads).
- Cada mensagem carrega um `msg_id` para reduzir replay.
- O usuário pode **rotacionar a chave** manualmente e **confirmar fingerprint**.

**Fingerprint**  
Ao ativar o E2EE, uma impressão digital da chave aparece no topo da sala. Confirme essa fingerprint com seu contato por um canal separado.

**Modelo de ameaça**  
O E2EE protege contra leitura do conteúdo pelo servidor e por terceiros em trânsito, **desde que** o segredo não seja vazado.  
Não protege contra: invasão do dispositivo do usuário, engenharia social ou comprometimento do navegador.

## Limitações Conhecidas
- Salas e presença são **voláteis** (memória) e não sobrevivem a reinício.
- Links de álbuns são privados, mas se forem compartilhados, qualquer pessoa com o link pode acessar enquanto válido.
- Uploads não possuem limitação de tamanho por padrão (recomenda‑se configurar em produção).
# darkchat-priv

"""Function Serverless de autenticação por CPF do AutoGiro.

Fluxo:
  1. Valida o CPF recebido (formato e dígitos verificadores).
  2. Consulta a existência e o status do cliente na view `auth.clients` do banco
     do OS Service (Neon), com um usuário somente leitura.
  3. Gera e devolve um token JWT válido para consumo das APIs protegidas.

O token é assinado em HS256 com o mesmo segredo configurado no Kong e nos
microsserviços. O Kong valida a assinatura antes de rotear; cada serviço
revalida e resolve o cliente pelo `sub` do token, que carrega o CPF normalizado.
"""

import contextlib
import json
import logging
import os
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import psycopg

from src import cpf as cpf_utils

logger = logging.getLogger()
logger.setLevel(os.getenv("LOG_LEVEL", "INFO"))

JWT_ALGORITHM = "HS256"
TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "60"))

# Conexão criada fora do handler para ser reaproveitada entre invocações
# na mesma execution environment (reduz o custo do cold start).
_connection: psycopg.Connection | None = None


def _get_connection() -> psycopg.Connection:
    """Devolve uma conexão viva com o banco, reconectando se necessário."""
    global _connection

    if _connection is None or _connection.closed:
        database_url = os.environ["DATABASE_URL"]
        # autocommit: a Lambda só lê. Sem ele, cada consulta abre uma transação que
        # fica aberta entre invocações, e um erro deixaria a conexão em estado de
        # falha para todas as chamadas seguintes do mesmo ambiente.
        _connection = psycopg.connect(database_url, connect_timeout=5, autocommit=True)

    return _connection


def _discard_connection() -> None:
    """Descarta a conexão depois de um erro: a próxima invocação abre uma nova."""
    global _connection

    if _connection is not None:
        with contextlib.suppress(psycopg.Error):
            _connection.close()
    _connection = None


def _response(status: int, body: dict[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, ensure_ascii=False),
    }


def _parse_body(event: dict[str, Any]) -> dict[str, Any]:
    """Extrai o corpo JSON do evento (Function URL ou API Gateway)."""
    raw = event.get("body")

    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw

    try:
        body = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
    # Um JSON válido que não é objeto (lista, número, texto) também é corpo inválido.
    return body if isinstance(body, dict) else {}


def _find_client(document: str) -> dict[str, Any] | None:
    """Busca o cliente pelo CPF normalizado (o banco armazena só os dígitos).

    Traz `is_active` na mesma consulta: o handler precisa distinguir cliente
    inexistente de cliente bloqueado, e são respostas diferentes.
    """
    query = (
        "SELECT id, name, cpf_cnpj, is_active FROM clients WHERE cpf_cnpj = %s LIMIT 1"
    )

    with _get_connection().cursor() as cursor:
        cursor.execute(query, (document,))
        row = cursor.fetchone()

    if row is None:
        return None

    return {"id": row[0], "name": row[1], "cpf_cnpj": row[2], "is_active": row[3]}


def _issue_token(client: dict[str, Any]) -> tuple[str, int]:
    """Assina o JWT. O `sub` carrega o CPF, consumido pela API principal."""
    expires_in = TOKEN_EXPIRE_MINUTES * 60
    now = datetime.now(UTC)

    payload = {
        "sub": client["cpf_cnpj"],
        "client_id": client["id"],
        "name": client["name"],
        "iat": now,
        "exp": now + timedelta(minutes=TOKEN_EXPIRE_MINUTES),
        "iss": "autogiro-auth",
    }

    token = jwt.encode(payload, os.environ["JWT_SECRET"], algorithm=JWT_ALGORITHM)
    return token, expires_in


def handler(event: dict[str, Any], _context: Any = None) -> dict[str, Any]:
    """Ponto de entrada da Lambda.

    Espera `{"cpf": "12345678909"}` — com ou sem máscara.
    """
    body = _parse_body(event)
    document = body.get("cpf", "")

    if not document:
        return _response(400, {"detail": "O campo 'cpf' é obrigatório"})
    if not isinstance(document, str):
        return _response(400, {"detail": "O campo 'cpf' deve ser texto"})

    if not cpf_utils.is_valid(document):
        # Não distingue "formato inválido" de "não cadastrado" na mensagem,
        # para não permitir enumeração de CPFs cadastrados.
        logger.info("Tentativa de autenticação com CPF inválido")
        return _response(401, {"detail": "CPF inválido ou não cadastrado"})

    normalized = cpf_utils.normalize(document)

    try:
        client = _find_client(normalized)
    except psycopg.Error:
        logger.exception("Falha ao consultar o banco de dados")
        _discard_connection()
        return _response(503, {"detail": "Serviço temporariamente indisponível"})

    if client is None:
        logger.info("Tentativa de autenticação com CPF não cadastrado")
        return _response(401, {"detail": "CPF inválido ou não cadastrado"})

    if not client["is_active"]:
        # Resposta distinta do 401 de propósito: aqui o cliente existe e o
        # documento é válido, então não há risco de enumeração — o que falta é
        # autorização, não autenticação. Um cliente bloqueado precisa saber que
        # o problema é o cadastro, para procurar a oficina.
        logger.info("Cliente %s está inativo; token não emitido", client["id"])
        return _response(403, {"detail": "Cadastro inativo. Procure a oficina."})

    token, expires_in = _issue_token(client)

    logger.info("Token emitido para o cliente %s", client["id"])

    return _response(
        200,
        {
            "access_token": token,
            "token_type": "bearer",
            "expires_in": expires_in,
        },
    )

"""Function Serverless de autenticação por CPF do AutoGiro.

Fluxo (requisito R3 do Tech Challenge Fase 3):
  1. Valida o CPF recebido (formato e dígitos verificadores).
  2. Consulta a existência e o status do cliente no banco gerenciado (Neon).
  3. Gera e devolve um token JWT válido para consumo das APIs protegidas.

O token é assinado em HS256 com o mesmo segredo configurado na aplicação
principal (autogiro-app). O Kong Gateway valida a assinatura antes de rotear;
a API resolve o cliente pelo `sub` do token, que carrega o CPF normalizado.
"""

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
        _connection = psycopg.connect(database_url, connect_timeout=5)

    return _connection


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
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def _find_client(document: str) -> dict[str, Any] | None:
    """Busca o cliente pelo CPF normalizado (o banco armazena só os dígitos)."""
    query = "SELECT id, name, cpf_cnpj FROM clients WHERE cpf_cnpj = %s LIMIT 1"

    with _get_connection().cursor() as cursor:
        cursor.execute(query, (document,))
        row = cursor.fetchone()

    if row is None:
        return None

    return {"id": row[0], "name": row[1], "cpf_cnpj": row[2]}


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
        return _response(503, {"detail": "Serviço temporariamente indisponível"})

    if client is None:
        logger.info("Tentativa de autenticação com CPF não cadastrado")
        return _response(401, {"detail": "CPF inválido ou não cadastrado"})

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

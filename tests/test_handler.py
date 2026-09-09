import json
from unittest.mock import MagicMock

import jwt
import psycopg
import pytest

from src import handler as handler_module

CPF_VALIDO = "52998224725"
CPF_INVALIDO = "52998224724"
JWT_SECRET = "segredo-de-teste-com-tamanho-suficiente"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", JWT_SECRET)
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@host/db")
    monkeypatch.setenv("ACCESS_TOKEN_EXPIRE_MINUTES", "60")
    monkeypatch.setattr(handler_module, "TOKEN_EXPIRE_MINUTES", 60)
    # Zera a conexão reaproveitada entre invocações.
    monkeypatch.setattr(handler_module, "_connection", None)


def _mock_connection(monkeypatch, row):
    """Injeta uma conexão falsa que devolve `row` na consulta do cliente.

    A linha tem a forma `(id, name, cpf_cnpj, is_active)` — a mesma ordem do
    SELECT em `_find_client`.
    """
    cursor = MagicMock()
    cursor.fetchone.return_value = row
    cursor.__enter__ = MagicMock(return_value=cursor)
    cursor.__exit__ = MagicMock(return_value=False)

    connection = MagicMock()
    connection.cursor.return_value = cursor
    connection.closed = False

    monkeypatch.setattr(handler_module, "_get_connection", lambda: connection)
    return connection


def _body(response):
    return json.loads(response["body"])


class TestValidacaoDeEntrada:
    def test_cpf_ausente(self):
        response = handler_module.handler({"body": json.dumps({})})

        assert response["statusCode"] == 400
        assert "obrigatório" in _body(response)["detail"]

    def test_body_ausente(self):
        response = handler_module.handler({})

        assert response["statusCode"] == 400

    def test_body_com_json_malformado(self):
        response = handler_module.handler({"body": "{nao-e-json"})

        assert response["statusCode"] == 400

    def test_body_como_dict(self, monkeypatch):
        _mock_connection(monkeypatch, (1, "Maria", CPF_VALIDO, True))

        response = handler_module.handler({"body": {"cpf": CPF_VALIDO}})

        assert response["statusCode"] == 200

    def test_cpf_com_digito_verificador_invalido(self):
        response = handler_module.handler({"body": json.dumps({"cpf": CPF_INVALIDO})})

        assert response["statusCode"] == 401
        assert _body(response)["detail"] == "CPF inválido ou não cadastrado"


class TestAutenticacao:
    def test_cliente_nao_cadastrado(self, monkeypatch):
        _mock_connection(monkeypatch, None)

        response = handler_module.handler({"body": json.dumps({"cpf": CPF_VALIDO})})

        assert response["statusCode"] == 401
        # Mesma mensagem do CPF inválido: evita enumeração de cadastros.
        assert _body(response)["detail"] == "CPF inválido ou não cadastrado"

    def test_cliente_encontrado_recebe_token(self, monkeypatch):
        _mock_connection(monkeypatch, (42, "Maria Silva", CPF_VALIDO, True))

        response = handler_module.handler({"body": json.dumps({"cpf": CPF_VALIDO})})
        body = _body(response)

        assert response["statusCode"] == 200
        assert body["token_type"] == "bearer"
        assert body["expires_in"] == 3600
        assert body["access_token"]

    def test_token_carrega_o_cpf_no_subject(self, monkeypatch):
        _mock_connection(monkeypatch, (42, "Maria Silva", CPF_VALIDO, True))

        response = handler_module.handler({"body": json.dumps({"cpf": CPF_VALIDO})})
        payload = jwt.decode(_body(response)["access_token"], JWT_SECRET, algorithms=["HS256"])

        assert payload["sub"] == CPF_VALIDO
        assert payload["client_id"] == 42
        assert payload["name"] == "Maria Silva"
        assert payload["iss"] == "autogiro-auth"

    def test_cpf_com_mascara_e_normalizado_na_consulta(self, monkeypatch):
        connection = _mock_connection(monkeypatch, (1, "Maria", CPF_VALIDO, True))

        response = handler_module.handler({"body": json.dumps({"cpf": "529.982.247-25"})})

        assert response["statusCode"] == 200
        # A consulta usa apenas os dígitos, como o banco armazena.
        cursor = connection.cursor.return_value
        assert cursor.execute.call_args[0][1] == (CPF_VALIDO,)


class TestFalhaDeBanco:
    def test_erro_de_banco_devolve_503(self, monkeypatch):
        def _falha():
            raise psycopg.OperationalError("conexão recusada")

        monkeypatch.setattr(handler_module, "_get_connection", _falha)

        response = handler_module.handler({"body": json.dumps({"cpf": CPF_VALIDO})})

        assert response["statusCode"] == 503
        assert "indisponível" in _body(response)["detail"]


class TestConexao:
    def test_reaproveita_conexao_entre_invocacoes(self, monkeypatch):
        criadas = []

        def _connect(url, **kwargs):
            connection = MagicMock()
            connection.closed = False
            criadas.append(connection)
            return connection

        monkeypatch.setattr(psycopg, "connect", _connect)

        primeira = handler_module._get_connection()
        segunda = handler_module._get_connection()

        assert primeira is segunda
        assert len(criadas) == 1

    def test_reconecta_quando_a_conexao_esta_fechada(self, monkeypatch):
        fechada = MagicMock()
        fechada.closed = True
        monkeypatch.setattr(handler_module, "_connection", fechada)

        nova = MagicMock()
        nova.closed = False
        monkeypatch.setattr(psycopg, "connect", lambda url, **kwargs: nova)

        assert handler_module._get_connection() is nova


class TestStatusDoCliente:
    """O enunciado pede consultar a existência **e o status** do cliente.

    Cliente inativo existe na base e tem CPF válido, mas não pode obter token.
    """

    def test_cliente_inativo_recebe_403(self, monkeypatch):
        _mock_connection(monkeypatch, (7, "Transportes Lima ME", CPF_VALIDO, False))

        resposta = handler_module.handler({"body": f'{{"cpf": "{CPF_VALIDO}"}}'})

        assert resposta["statusCode"] == 403

    def test_a_mensagem_orienta_o_cliente_bloqueado(self, monkeypatch):
        """Ao contrário do 401, aqui revelar o motivo é seguro e útil.

        O documento é válido e o cadastro existe, então não há o que enumerar —
        e o cliente precisa saber que deve procurar a oficina.
        """
        _mock_connection(monkeypatch, (7, "Transportes Lima ME", CPF_VALIDO, False))

        resposta = handler_module.handler({"body": f'{{"cpf": "{CPF_VALIDO}"}}'})

        assert "inativo" in _body(resposta)["detail"].lower()

    def test_cliente_inativo_nao_recebe_token(self, monkeypatch):
        _mock_connection(monkeypatch, (7, "Bloqueado", CPF_VALIDO, False))

        resposta = handler_module.handler({"body": f'{{"cpf": "{CPF_VALIDO}"}}'})

        assert "access_token" not in _body(resposta)

    def test_cliente_ativo_segue_recebendo_token(self, monkeypatch):
        """Guarda contra regressão: o caminho normal não pode ter mudado."""
        _mock_connection(monkeypatch, (1, "Maria", CPF_VALIDO, True))

        resposta = handler_module.handler({"body": f'{{"cpf": "{CPF_VALIDO}"}}'})

        assert resposta["statusCode"] == 200
        assert _body(resposta)["access_token"]

    def test_inativo_e_distinguido_de_inexistente(self, monkeypatch):
        """403 para bloqueado, 401 para inexistente — são situações diferentes."""
        _mock_connection(monkeypatch, None)
        inexistente = handler_module.handler({"body": f'{{"cpf": "{CPF_VALIDO}"}}'})

        _mock_connection(monkeypatch, (7, "Bloqueado", CPF_VALIDO, False))
        bloqueado = handler_module.handler({"body": f'{{"cpf": "{CPF_VALIDO}"}}'})

        assert inexistente["statusCode"] == 401
        assert bloqueado["statusCode"] == 403

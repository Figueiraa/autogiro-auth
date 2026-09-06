import pytest

from src import cpf


class TestNormalize:
    def test_remove_mascara(self):
        assert cpf.normalize("529.982.247-25") == "52998224725"

    def test_remove_espacos_e_caracteres_diversos(self):
        assert cpf.normalize(" 529 982 247 25 ") == "52998224725"

    def test_entrada_vazia(self):
        assert cpf.normalize("") == ""

    def test_entrada_none(self):
        assert cpf.normalize(None) == ""


class TestIsValid:
    @pytest.mark.parametrize(
        "documento",
        [
            "52998224725",
            "529.982.247-25",
            "16899535009",
            "11144477735",
        ],
    )
    def test_cpfs_validos(self, documento):
        assert cpf.is_valid(documento) is True

    @pytest.mark.parametrize(
        "documento",
        [
            "52998224724",  # último dígito verificador errado
            "52998224715",  # penúltimo dígito verificador errado
            "12345678901",  # sequência arbitrária
        ],
    )
    def test_digito_verificador_invalido(self, documento):
        assert cpf.is_valid(documento) is False

    @pytest.mark.parametrize(
        "documento",
        ["00000000000", "11111111111", "99999999999"],
    )
    def test_digitos_repetidos_sao_invalidos(self, documento):
        # Passam no cálculo do módulo 11, mas são inválidos por convenção.
        assert cpf.is_valid(documento) is False

    @pytest.mark.parametrize(
        "documento",
        ["", "123", "529982247250", "abcdefghijk"],
    )
    def test_tamanho_invalido(self, documento):
        assert cpf.is_valid(documento) is False

    def test_none(self):
        assert cpf.is_valid(None) is False

    def test_cpf_cujo_digito_verificador_e_zero(self):
        # Exercita o ramo em que o resto do módulo 11 é 10 e o dígito vira "0".
        assert cpf.is_valid("16899535009") is True


class TestFormatMasked:
    def test_aplica_mascara(self):
        assert cpf.format_masked("52998224725") == "529.982.247-25"

    def test_entrada_ja_mascarada(self):
        assert cpf.format_masked("529.982.247-25") == "529.982.247-25"

    def test_tamanho_invalido_retorna_normalizado(self):
        assert cpf.format_masked("123") == "123"

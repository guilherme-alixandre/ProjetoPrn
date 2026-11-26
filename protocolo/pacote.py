"""
Formato do pacote da rede PRN e suas validacoes.

Define os dois tipos de pacote que trafegam pela rede:
  - "leitura": transporta uma leitura de temperatura do Sensor ate o Coletor
    (campos origem, destino, sequencia, ttl, temperatura_c, criado_em).
  - "pulso": pacote de controle usado so para o mecanismo de pulso de
    vizinhanca (deteccao de no inativo), sem dados de leitura.

Cada pacote carrega um campo "hmac" (HMAC-SHA256 com chave fixa compartilhada)
recalculado a cada salto, usado para detectar corrupcao/adulteracao e
autenticar a origem sem precisar de handshake.
"""
import hashlib
import hmac as hmac_lib
import time

# Campos usados para calcular a assinatura HMAC. Um pulso nao tem sequencia/
# ttl/temperatura_c/criado_em, entao esses campos entram como string vazia
# (ver _base_assinatura) — assim a mesma funcao assina os dois tipos de pacote.
CAMPOS_ASSINATURA = ("tipo", "origem", "destino", "sequencia", "ttl", "temperatura_c", "criado_em")
CAMPOS_OBRIGATORIOS_LEITURA = ("tipo", "origem", "destino", "sequencia", "ttl", "temperatura_c", "criado_em")
CAMPOS_OBRIGATORIOS_PULSO = ("tipo", "origem", "destino")
TIPOS_VALIDOS = ("leitura", "pulso")


def _base_assinatura(dados):
    """Monta a string canonica (campos separados por '|') sobre a qual o HMAC e calculado."""
    partes = [str(dados.get(campo, "")) for campo in CAMPOS_ASSINATURA]
    return "|".join(partes)


def calcular_hmac(chave, dados):
    """Calcula o HMAC-SHA256 (16 hex chars) de um pacote usando a chave compartilhada."""
    base = _base_assinatura(dados)
    return hmac_lib.new(chave.encode("utf-8"), base.encode("utf-8"), hashlib.sha256).hexdigest()[:16]


def montar_pacote(origem, destino, sequencia, ttl, temperatura_c, chave, criado_em=None):
    """Cria um pacote de leitura pronto para envio, com HMAC ja calculado.

    'criado_em' e o timestamp de geracao da leitura (preservado sem mudar nos
    saltos seguintes); se omitido, usa o instante atual. Quem encaminha o
    pacote (retransmissor) deve chamar esta funcao de novo com o 'criado_em'
    original e o 'ttl' ja decrementado, para que o HMAC seja recalculado.
    """
    if criado_em is None:
        criado_em = time.time()
    dados = {
        "tipo": "leitura",
        "origem": origem,
        "destino": destino,
        "sequencia": sequencia,
        "ttl": ttl,
        "temperatura_c": temperatura_c,
        "criado_em": criado_em,
    }
    dados["hmac"] = calcular_hmac(chave, dados)
    return dados


def montar_pulso(origem, destino, chave):
    """Cria um pacote de controle 'pulso' (sinal de vida) com HMAC calculado."""
    dados = {"tipo": "pulso", "origem": origem, "destino": destino}
    dados["hmac"] = calcular_hmac(chave, dados)
    return dados


class PacoteInvalido(Exception):
    """Levantada por validar_pacote(); .motivo identifica a razao do descarte."""

    def __init__(self, motivo):
        super().__init__(motivo)
        self.motivo = motivo


def validar_pacote(dados, chave, tempo_de_vida_maximo=None):
    """Valida formato, tipos, HMAC e (para leituras) idade do pacote.

    Levanta PacoteInvalido (com .motivo em 'formato', 'ttl_negativo', 'integridade'
    ou 'expirado'); caso contrario retorna o proprio dict recebido.
    """
    if not isinstance(dados, dict):
        raise PacoteInvalido("formato")

    tipo = dados.get("tipo")
    if tipo not in TIPOS_VALIDOS:
        raise PacoteInvalido("formato")

    if "hmac" not in dados or not isinstance(dados["hmac"], str):
        raise PacoteInvalido("formato")

    if tipo == "pulso":
        # Pulso so precisa dos campos minimos (origem/destino); sem
        # sequencia/ttl/temperatura_c/criado_em, nao ha o que validar alem disso.
        for campo in CAMPOS_OBRIGATORIOS_PULSO:
            if campo not in dados or not isinstance(dados[campo], str):
                raise PacoteInvalido("formato")
    else:
        # Leitura exige todos os campos de dados, com os tipos corretos.
        for campo in CAMPOS_OBRIGATORIOS_LEITURA:
            if campo not in dados:
                raise PacoteInvalido("formato")

        if not isinstance(dados["origem"], str) or not isinstance(dados["destino"], str):
            raise PacoteInvalido("formato")
        if not isinstance(dados["sequencia"], int) or isinstance(dados["sequencia"], bool):
            raise PacoteInvalido("formato")
        if not isinstance(dados["ttl"], int) or isinstance(dados["ttl"], bool):
            raise PacoteInvalido("formato")
        if not isinstance(dados["temperatura_c"], (int, float)) or isinstance(dados["temperatura_c"], bool):
            raise PacoteInvalido("formato")
        if not isinstance(dados["criado_em"], (int, float)) or isinstance(dados["criado_em"], bool):
            raise PacoteInvalido("formato")

        # TTL negativo e invalido mesmo antes de checar o HMAC (ver secao 5 do enunciado).
        if dados["ttl"] < 0:
            raise PacoteInvalido("ttl_negativo")

    # Autenticidade/integridade: recalcula o HMAC sobre os campos recebidos e
    # compara com o que veio no pacote (compare_digest evita timing attack).
    hmac_esperado = calcular_hmac(chave, dados)
    if not hmac_lib.compare_digest(dados["hmac"], hmac_esperado):
        raise PacoteInvalido("integridade")

    # TTL duplo: alem do limite de saltos, descarta leituras "velhas demais".
    if tipo == "leitura" and tempo_de_vida_maximo is not None:
        idade = time.time() - dados["criado_em"]
        if idade > tempo_de_vida_maximo:
            raise PacoteInvalido("expirado")

    return dados

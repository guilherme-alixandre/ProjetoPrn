"""
Logica de cada papel da rede PRN: Sensor (S), Retransmissor (R1-R4) e Coletor (C).

Todos os nos compartilham a mesma topologia fixa (config/topologia.json, um
socket UDP por no, vizinhos pre-definidos). R1-R4 e C tambem compartilham o
mesmo loop de recepcao (_executar_no_receptor), que cuida de validar pacotes,
mandar/receber o pulso de vizinhanca e detectar vizinho inativo — cada papel
so precisa dizer o que fazer com uma leitura valida (encaminhar ou salvar).
"""
import csv
import json
import os
import random
import socket
import time
from datetime import datetime, timezone

from .logger import LoggerJSONL
from .pacote import PacoteInvalido, montar_pacote, montar_pulso, validar_pacote

TAMANHO_BUFFER = 4096


def carregar_topologia(caminho):
    """Le o JSON de topologia (portas, vizinhos e parametros do protocolo)."""
    with open(caminho, "r", encoding="utf-8") as arquivo:
        return json.load(arquivo)


def criar_socket(host, porta):
    """Cria e vincula (bind) um socket UDP a uma porta fixa, usada tanto para
    enviar quanto para receber — e o que permite identificar o remetente de
    cada salto pela porta de origem do datagrama."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((host, porta))
    return sock


def _enderecos_permitidos(topologia, nomes):
    """Converte uma lista de nomes de nos (ex: ['S']) no conjunto de
    enderecos (host, porta) correspondente, usado para checar remetentes."""
    return {(topologia[nome]["host"], topologia[nome]["porta"]) for nome in nomes}


def _nome_por_endereco(topologia, nomes, endereco):
    """Faz o caminho inverso de _enderecos_permitidos: dado um (host, porta)
    recebido num datagrama, descobre de qual no da lista ele veio."""
    for nome in nomes:
        cfg = topologia[nome]
        if (cfg["host"], cfg["porta"]) == endereco:
            return nome
    return None


def _decodificar_e_validar(dados_brutos, chave, tempo_de_vida_maximo):
    """Faz o parse JSON + validacao de formato/tipos/HMAC/idade.

    Retorna (pacote, None) em caso de sucesso, ou (None, motivo) em caso de erro,
    onde motivo e 'formato', 'ttl_negativo', 'integridade' ou 'expirado'.
    """
    try:
        dados = json.loads(dados_brutos.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None, "formato"

    try:
        pacote = validar_pacote(dados, chave, tempo_de_vida_maximo)
    except PacoteInvalido as exc:
        return None, exc.motivo

    return pacote, None


def _acao_para_motivo(motivo):
    """Traduz o motivo de um PacoteInvalido para a acao de log correspondente."""
    if motivo == "integridade":
        return "erro_integridade"
    if motivo == "expirado":
        return "descarte_expirado"
    return "descarte_formato"


def rodar_sensor(topologia, exec_id, sequencia_inicial=1, quantidade=10,
                  intervalo=2.0, ttl_inicial=3, duplicar_leitura=False):
    """Papel do Sensor (S): gera 'quantidade' leituras fictícias, uma a cada
    'intervalo' segundos, e envia uma copia independente para cada caminho
    (R1 e R3). Tambem manda um pulso de vizinhanca a cada destino a cada
    leitura. 'duplicar_leitura' (usado no teste T7) faz a mesma leitura ser
    enviada duas vezes ao mesmo destino, para testar a deduplicacao."""
    cfg = topologia["S"]
    chave = topologia["parametros"]["chave_hmac"]
    sock = criar_socket(cfg["host"], cfg["porta"])
    destinos = [(nome, topologia[nome]["host"], topologia[nome]["porta"])
                for nome in cfg["envia_para"]]
    logger = LoggerJSONL("S", exec_id)

    try:
        for indice in range(quantidade):
            sequencia = sequencia_inicial + indice
            temperatura_c = round(random.uniform(15.0, 35.0), 1)
            ttl_envio = ttl_inicial - 1
            criado_em = time.time()

            logger.registrar("leitura_gerada", origem="S", sequencia=sequencia,
                              ttl=ttl_inicial, detalhe=f"temperatura={temperatura_c}")

            copias = [destinos[0], destinos[0]] if duplicar_leitura else destinos

            for nome_destino, host_destino, porta_destino in copias:
                pacote = montar_pacote("S", "C", sequencia, ttl_envio, temperatura_c, chave,
                                        criado_em=criado_em)
                logger.registrar("tentativa_envio", origem="S", sequencia=sequencia,
                                  proximo_no=nome_destino, ttl=ttl_envio)
                try:
                    sock.sendto(json.dumps(pacote).encode("utf-8"),
                                (host_destino, porta_destino))
                except OSError as exc:
                    logger.registrar("erro_envio", origem="S", sequencia=sequencia,
                                      proximo_no=nome_destino, ttl=ttl_envio, detalhe=str(exc))
                    continue
                logger.registrar("envio_aceito", origem="S", sequencia=sequencia,
                                  proximo_no=nome_destino, ttl=ttl_envio)

            # Pulso de vizinhanca: o Sensor nunca recebe, entao ele aproveita o
            # proprio loop de geracao de leituras para avisar que esta vivo,
            # em vez de precisar de uma thread ou timeout separado.
            for nome_destino, host_destino, porta_destino in destinos:
                pulso = montar_pulso("S", nome_destino, chave)
                try:
                    sock.sendto(json.dumps(pulso).encode("utf-8"), (host_destino, porta_destino))
                    logger.registrar("pulso_enviado", proximo_no=nome_destino)
                except OSError as exc:
                    logger.registrar("erro_envio", proximo_no=nome_destino, detalhe=str(exc))

            if indice < quantidade - 1:
                time.sleep(intervalo)
    finally:
        logger.fechar()
        sock.close()


def _executar_no_receptor(nome, topologia, exec_id, criar_manipulador):
    """Loop generico para R1-R4 e C: recebe leituras/pulsos, envia pulso periodico
    para o proximo no (se houver) e monitora a inatividade dos vizinhos de 'recebe_de'.

    'criar_manipulador(sock, logger, proximo_nome, proximo_cfg, chave)' deve devolver
    uma funcao manipular(pacote, remetente_nome) chamada para cada pacote tipo='leitura'
    valido — e quem decide o que fazer com ele (encaminhar em R1-R4, salvar em C).
    """
    cfg = topologia[nome]
    parametros = topologia["parametros"]
    chave = parametros["chave_hmac"]
    intervalo_pulso = parametros["intervalo_pulso"]
    tempo_de_vida_maximo = parametros["tempo_de_vida_maximo"]
    limite_inatividade = intervalo_pulso * parametros["tolerancia_pulsos_perdidos"]

    sock = criar_socket(cfg["host"], cfg["porta"])
    sock.settimeout(intervalo_pulso)
    permitidos = _enderecos_permitidos(topologia, cfg["recebe_de"])
    proximo_nome = cfg["envia_para"][0] if cfg["envia_para"] else None
    proximo_cfg = topologia[proximo_nome] if proximo_nome else None
    logger = LoggerJSONL(nome, exec_id)

    manipular_leitura = criar_manipulador(sock, logger, proximo_nome, proximo_cfg, chave)

    # 'ultimo_visto'/'inativo' guardam, por vizinho de quem este no recebe
    # dados, quando foi a ultima vez que algo (leitura ou pulso) chegou dele,
    # e se ja foi declarado inativo (para nao logar vizinho_inativo repetido).
    agora0 = time.time()
    ultimo_visto = {vizinho: agora0 for vizinho in cfg["recebe_de"]}
    inativo = {vizinho: False for vizinho in cfg["recebe_de"]}
    ultimo_envio_pulso = agora0

    def _executar_housekeeping(agora):
        """Roda a cada volta do loop: manda um pulso pro proximo no (se ja
        passou 'intervalo_pulso' desde o ultimo) e verifica se algum vizinho
        de quem este no recebe dados esta em silencio ha tempo demais."""
        nonlocal ultimo_envio_pulso
        if proximo_nome is not None and agora - ultimo_envio_pulso >= intervalo_pulso:
            pulso = montar_pulso(nome, proximo_nome, chave)
            try:
                sock.sendto(json.dumps(pulso).encode("utf-8"),
                            (proximo_cfg["host"], proximo_cfg["porta"]))
                logger.registrar("pulso_enviado", proximo_no=proximo_nome)
            except OSError as exc:
                logger.registrar("erro_envio", proximo_no=proximo_nome, detalhe=str(exc))
            ultimo_envio_pulso = agora

        for vizinho, visto_em in ultimo_visto.items():
            if not inativo[vizinho] and agora - visto_em > limite_inatividade:
                inativo[vizinho] = True
                logger.registrar("vizinho_inativo", remetente_salto=vizinho,
                                  detalhe=f"sem sinal ha {agora - visto_em:.1f}s")

    try:
        while True:
            # A checagem periodica (pulso + deteccao de inatividade) roda a cada
            # iteracao, nao so quando o socket fica OCIOSO: se um vizinho continuar
            # mandando pacotes dentro da janela de timeout, o recvfrom nunca estoura
            # e a checagem dos DEMAIS vizinhos (que podem estar mesmo inativos)
            # ficaria "esfomeada" se dependesse so do except socket.timeout.
            _executar_housekeeping(time.time())

            try:
                dados_brutos, endereco = sock.recvfrom(TAMANHO_BUFFER)
            except socket.timeout:
                continue
            except OSError as exc:
                # No Windows, mandar pra uma porta sem ninguem escutando pode
                # gerar um ConnectionResetError na recepcao seguinte (nao no
                # envio). Registra o erro e continua recebendo normalmente.
                logger.registrar("erro_recepcao", detalhe=str(exc))
                continue

            if endereco not in permitidos:
                logger.registrar("descarte_remetente",
                                  detalhe=f"endereco nao autorizado: {endereco[0]}:{endereco[1]}")
                continue

            remetente_nome = _nome_por_endereco(topologia, cfg["recebe_de"], endereco)
            agora = time.time()
            if inativo.get(remetente_nome):
                inativo[remetente_nome] = False
                logger.registrar("vizinho_reativado", remetente_salto=remetente_nome)
            ultimo_visto[remetente_nome] = agora

            pacote, motivo = _decodificar_e_validar(dados_brutos, chave, tempo_de_vida_maximo)
            if pacote is None:
                logger.registrar(_acao_para_motivo(motivo), remetente_salto=remetente_nome,
                                  detalhe=motivo)
                continue

            if pacote["tipo"] == "pulso":
                logger.registrar("pulso_recebido", remetente_salto=remetente_nome)
                continue

            manipular_leitura(pacote, remetente_nome)
    finally:
        logger.fechar()
        sock.close()


def rodar_retransmissor(nome, topologia, exec_id):
    """Papel de um retransmissor (R1, R2, R3 ou R4) — a mesma funcao serve
    para os quatro, parametrizada por 'nome' (cada um le seus proprios
    vizinhos/porta da topologia). Encaminha a primeira copia valida de cada
    leitura com TTL > 0, decrementando o TTL e recalculando o HMAC."""
    janela_dedup = topologia["parametros"]["janela_dedup_segundos"]

    def criar_manipulador(sock, logger, proximo_nome, proximo_cfg, chave):
        # 'encaminhados' guarda, por identificador (origem, sequencia), o
        # instante em que foi encaminhado — usado tanto para deduplicacao
        # quanto para purgar entradas antigas (janela de dedup com expiracao).
        encaminhados = {}

        def manipular(pacote, remetente_nome):
            """Processa uma leitura valida ja recebida: valida destino,
            descarta duplicata/TTL zerado, ou encaminha com TTL-1."""
            if pacote["destino"] != "C":
                logger.registrar("descarte_formato", origem=pacote["origem"],
                                  sequencia=pacote["sequencia"], remetente_salto=remetente_nome,
                                  ttl=pacote["ttl"], detalhe="destino invalido")
                return

            logger.registrar("recepcao", origem=pacote["origem"], sequencia=pacote["sequencia"],
                              remetente_salto=remetente_nome, ttl=pacote["ttl"])

            agora = time.time()
            identificador = (pacote["origem"], pacote["sequencia"])

            # Purga identificadores mais antigos que a janela de dedup, para a
            # memoria nao crescer sem limite numa execucao longa.
            for ident in [i for i, quando in encaminhados.items() if agora - quando > janela_dedup]:
                del encaminhados[ident]

            if identificador in encaminhados:
                logger.registrar("descarte_duplicata", origem=pacote["origem"],
                                  sequencia=pacote["sequencia"], remetente_salto=remetente_nome,
                                  ttl=pacote["ttl"])
                return

            if pacote["ttl"] == 0:
                logger.registrar("descarte_ttl", origem=pacote["origem"],
                                  sequencia=pacote["sequencia"], remetente_salto=remetente_nome,
                                  ttl=pacote["ttl"])
                return

            # Monta a copia de saida com TTL-1; criado_em e preservado (nao
            # muda nos saltos), so o hmac e recalculado por cima do novo TTL.
            ttl_saida = pacote["ttl"] - 1
            pacote_saida = montar_pacote(pacote["origem"], pacote["destino"], pacote["sequencia"],
                                          ttl_saida, pacote["temperatura_c"], chave,
                                          criado_em=pacote["criado_em"])

            logger.registrar("tentativa_envio", origem=pacote["origem"],
                              sequencia=pacote["sequencia"], proximo_no=proximo_nome, ttl=ttl_saida)
            try:
                sock.sendto(json.dumps(pacote_saida).encode("utf-8"),
                            (proximo_cfg["host"], proximo_cfg["porta"]))
            except OSError as exc:
                logger.registrar("erro_envio", origem=pacote["origem"],
                                  sequencia=pacote["sequencia"], proximo_no=proximo_nome,
                                  ttl=ttl_saida, detalhe=str(exc))
                return

            # So marca como encaminhado depois que o socket aceitou o envio
            # (um envio aceito nao comprova a chegada ao proximo no).
            encaminhados[identificador] = agora
            logger.registrar("envio_aceito", origem=pacote["origem"], sequencia=pacote["sequencia"],
                              proximo_no=proximo_nome, ttl=ttl_saida)
            logger.registrar("encaminhado", origem=pacote["origem"], sequencia=pacote["sequencia"],
                              remetente_salto=remetente_nome, proximo_no=proximo_nome, ttl=ttl_saida)

        return manipular

    _executar_no_receptor(nome, topologia, exec_id, criar_manipulador)


def rodar_coletor(topologia, exec_id, pasta_resultados="resultados"):
    """Papel do Coletor (C): recebe as leituras vindas de R2 e R4, salva a
    primeira copia de cada identificador (origem, sequencia) em
    resultados/leituras_<exec_id>.csv e registra as copias seguintes como
    duplicata."""
    janela_dedup = topologia["parametros"]["janela_dedup_segundos"]

    os.makedirs(pasta_resultados, exist_ok=True)
    caminho_csv = os.path.join(pasta_resultados, f"leituras_{exec_id}.csv")
    arquivo_novo = not os.path.exists(caminho_csv)
    arquivo_csv = open(caminho_csv, "a", newline="", encoding="utf-8")
    escritor = csv.writer(arquivo_csv)
    if arquivo_novo:
        escritor.writerow(["origem", "sequencia", "temperatura_c", "horario", "ultimo_retransmissor"])
        arquivo_csv.flush()

    def criar_manipulador(sock, logger, proximo_nome, proximo_cfg, chave):
        # 'recebidos' guarda, por identificador, o instante do primeiro
        # recebimento — mesma logica de janela de dedup usada nos retransmissores.
        recebidos = {}

        def manipular(pacote, remetente_nome):
            """Processa uma leitura valida ja recebida: salva a primeira
            copia em CSV, ou registra como duplicata."""
            if pacote["destino"] != "C":
                logger.registrar("descarte_formato", origem=pacote["origem"],
                                  sequencia=pacote["sequencia"], remetente_salto=remetente_nome,
                                  ttl=pacote["ttl"], detalhe="destino invalido")
                return

            logger.registrar("recepcao", origem=pacote["origem"], sequencia=pacote["sequencia"],
                              remetente_salto=remetente_nome, ttl=pacote["ttl"])

            agora = time.time()
            identificador = (pacote["origem"], pacote["sequencia"])

            for ident in [i for i, quando in recebidos.items() if agora - quando > janela_dedup]:
                del recebidos[ident]

            if identificador in recebidos:
                logger.registrar("duplicata_recebida", origem=pacote["origem"],
                                  sequencia=pacote["sequencia"], remetente_salto=remetente_nome,
                                  ttl=pacote["ttl"])
                return

            recebidos[identificador] = agora
            horario = datetime.now(timezone.utc).isoformat()
            escritor.writerow([pacote["origem"], pacote["sequencia"], pacote["temperatura_c"],
                                horario, remetente_nome])
            arquivo_csv.flush()
            logger.registrar("entrega_unica", origem=pacote["origem"], sequencia=pacote["sequencia"],
                              remetente_salto=remetente_nome, ttl=pacote["ttl"])

        return manipular

    try:
        _executar_no_receptor("C", topologia, exec_id, criar_manipulador)
    finally:
        arquivo_csv.close()

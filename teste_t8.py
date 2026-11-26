"""Teste extra T8 (fora do enunciado do PDF): pacotes com informacoes erradas.

Simula um remetente que fabrica pacotes incorretos na mao (nao usa o Sensor
legitimo) e envia direto para R1, confirmando que cada caso e descartado pelo
motivo certo e que nada disso chega ao coletor. Casos cobertos:

  a) destino errado        -> descarte_formato (detalhe: destino invalido)
  b) HMAC adulterado        -> erro_integridade
  c) TTL negativo            -> descarte_formato (detalhe: ttl_negativo)
  d) remetente nao autorizado -> descarte_remetente
"""
import json
import os
import subprocess
import sys
import time

from protocolo.no import carregar_topologia, criar_socket
from protocolo.pacote import montar_pacote

RAIZ = os.path.dirname(os.path.abspath(__file__))
EXECUTAR_NO = os.path.join(RAIZ, "executar_no.py")
PYTHON = sys.executable
EXEC_ID = "T8"

CASOS_ESPERADOS = [
    ("destino_errado", "descarte_formato", "destino invalido"),
    ("hmac_adulterado", "erro_integridade", None),
    ("ttl_negativo", "descarte_formato", "ttl_negativo"),
    ("remetente_nao_autorizado", "descarte_remetente", None),
]


def iniciar_no(nome):
    """Sobe um no legitimo (C, R2 ou R1) como subprocesso, igual ao orquestrador."""
    cmd = [PYTHON, EXECUTAR_NO, "--no", nome, "--exec-id", EXEC_ID]
    return subprocess.Popen(cmd, cwd=RAIZ)


def _encerrar(processo):
    """Derruba um processo-no ao final do teste."""
    if processo.poll() is None:
        processo.terminate()
        try:
            processo.wait(timeout=5)
        except subprocess.TimeoutExpired:
            processo.kill()
            processo.wait()


def enviar_cru(sock, host, porta, pacote):
    """Envia um dict de pacote ja pronto (sem passar pelo Sensor) via UDP."""
    sock.sendto(json.dumps(pacote).encode("utf-8"), (host, porta))


def montar_casos(chave):
    """Monta os 4 pacotes de teste, cada um valido em tudo exceto o detalhe
    que deveria causar o descarte (destino, HMAC, TTL ou remetente)."""
    return {
        "destino_errado": montar_pacote("S", "R3", 101, 2, 25.0, chave),
        "hmac_adulterado": _com_hmac_adulterado(montar_pacote("S", "C", 102, 2, 25.0, chave)),
        "ttl_negativo": montar_pacote("S", "C", 103, -1, 25.0, chave),
        "remetente_nao_autorizado": montar_pacote("S", "C", 104, 2, 25.0, chave),
    }


def _com_hmac_adulterado(pacote):
    """Troca o HMAC de um pacote valido por um valor obviamente errado."""
    pacote["hmac"] = "0" * len(pacote["hmac"])
    return pacote


def rodar():
    """Sobe C, R2 e R1, envia os 4 pacotes invalidos direto para R1 (3 deles
    fingindo ser o Sensor, o quarto de uma porta nao autorizada) e confere o
    resultado nos logs."""
    topologia = carregar_topologia(os.path.join(RAIZ, "config", "topologia.json"))
    chave = topologia["parametros"]["chave_hmac"]
    s_cfg = topologia["S"]
    r1_cfg = topologia["R1"]

    print("\n=== Teste T8 (fora do PDF) - pacotes com informacoes erradas ===")
    print("Sobe C, R2, R1 e envia pacotes fabricados na mao direto para R1.\n")

    processos = [iniciar_no("C"), iniciar_no("R2"), iniciar_no("R1")]
    time.sleep(1)

    sock_s = criar_socket(s_cfg["host"], s_cfg["porta"])
    sock_intruso = criar_socket(r1_cfg["host"], 7000)  # porta fora da lista de permitidos de R1

    casos = montar_casos(chave)
    for nome_caso in ("destino_errado", "hmac_adulterado", "ttl_negativo"):
        enviar_cru(sock_s, r1_cfg["host"], r1_cfg["porta"], casos[nome_caso])
        print(f"  enviado (remetente correto): {nome_caso}")
        time.sleep(0.3)

    enviar_cru(sock_intruso, r1_cfg["host"], r1_cfg["porta"], casos["remetente_nao_autorizado"])
    print("  enviado (remetente errado, pacote por outro lado valido): remetente_nao_autorizado")

    time.sleep(2)
    sock_s.close()
    sock_intruso.close()
    for processo in processos:
        _encerrar(processo)

    return conferir_resultados()


def conferir_resultados():
    """Le logs/T8/R1.jsonl e logs/T8/C.jsonl e verifica, para cada caso,
    se a acao/detalhe esperados aparecem no log — e que nada chegou ao
    coletor. Imprime PASSOU/FALHOU por caso e devolve True so se tudo passou."""
    caminho_r1 = os.path.join(RAIZ, "logs", EXEC_ID, "R1.jsonl")
    eventos_r1 = []
    if os.path.exists(caminho_r1):
        with open(caminho_r1, encoding="utf-8") as arquivo:
            eventos_r1 = [json.loads(linha) for linha in arquivo]

    print("\n--- Resultado T8 ---")
    tudo_ok = True
    for nome_caso, acao_esperada, trecho_detalhe in CASOS_ESPERADOS:
        encontrado = any(
            ev["acao"] == acao_esperada
            and (trecho_detalhe is None or trecho_detalhe in (ev.get("detalhe") or ""))
            for ev in eventos_r1
        )
        tudo_ok &= encontrado
        marca = "PASSOU" if encontrado else "FALHOU"
        detalhe_txt = f" (detalhe contem {trecho_detalhe!r})" if trecho_detalhe else ""
        print(f"  [{marca}] {nome_caso}: esperado acao='{acao_esperada}'{detalhe_txt}")

    caminho_c = os.path.join(RAIZ, "logs", EXEC_ID, "C.jsonl")
    entregas_c = 0
    if os.path.exists(caminho_c):
        with open(caminho_c, encoding="utf-8") as arquivo:
            entregas_c = sum(
                1 for linha in arquivo
                if json.loads(linha)["acao"] in ("entrega_unica", "duplicata_recebida")
            )
    nada_chegou = entregas_c == 0
    tudo_ok &= nada_chegou
    marca = "PASSOU" if nada_chegou else "FALHOU"
    print(f"  [{marca}] nada chegou ao coletor (entregas registradas em C: {entregas_c})")

    print(f"\n{'TODOS OS CASOS PASSARAM' if tudo_ok else 'ALGUM CASO FALHOU'} (T8)")
    print(f"Logs completos em logs/{EXEC_ID}/\n")
    return tudo_ok


if __name__ == "__main__":
    sucesso = rodar()
    sys.exit(0 if sucesso else 1)

"""
Orquestrador dos testes T1-T7 do enunciado.

Sobe os processos (S, R1-R4, C) necessarios para cada teste via subprocess,
aguarda o sensor terminar, e para os testes de falha (T3-T5) derruba o(s)
no(s) indicado(s) no meio da execucao e retoma o sensor a partir da leitura
11. Tambem desenha no terminal a topologia (com nos derrubados marcados) e
imprime um resumo das simulacoes, lido direto dos logs JSON Lines gerados.

Uso:
    python orquestrador.py --teste T1   (ou T2..T7)
    python orquestrador.py --todos      (roda T1 a T7 em sequencia)
"""
import argparse
import glob
import json
import os
import subprocess
import sys
import time

PYTHON = sys.executable
RAIZ = os.path.dirname(os.path.abspath(__file__))
EXECUTAR_NO = os.path.join(RAIZ, "executar_no.py")

# Configuracao de cada teste "padrao" (T1-T6): quais nos sobem, quantas
# leituras o sensor gera, TTL inicial, e quais nos derrubar (se algum) no
# meio da execucao. T7 tem um fluxo proprio (rodar_t7) por ser um caso
# especial de leitura unica duplicada, nao uma serie de leituras periodicas.
TESTES = {
    "T1": {"nos": ["C", "R2", "R1"], "quantidade": 10, "ttl_inicial": 3, "derrubar": []},
    "T2": {"nos": ["C", "R2", "R4", "R1", "R3"], "quantidade": 10, "ttl_inicial": 3, "derrubar": []},
    "T3": {"nos": ["C", "R2", "R4", "R1", "R3"], "quantidade": 10, "ttl_inicial": 3, "derrubar": ["R1"]},
    "T4": {"nos": ["C", "R2", "R4", "R1", "R3"], "quantidade": 10, "ttl_inicial": 3, "derrubar": ["R2"]},
    "T5": {"nos": ["C", "R2", "R4", "R1", "R3"], "quantidade": 10, "ttl_inicial": 3, "derrubar": ["R2", "R4"]},
    "T6": {"nos": ["C", "R2", "R4", "R1", "R3"], "quantidade": 10, "ttl_inicial": 2, "derrubar": []},
}
ORDEM_TESTES = [*TESTES.keys(), "T7"]


def iniciar_no(nome, exec_id, **kwargs):
    """Sobe um no (executar_no.py) como subprocesso. kwargs vira flags de
    linha de comando (ex: quantidade=10 -> --quantidade 10)."""
    cmd = [PYTHON, EXECUTAR_NO, "--no", nome, "--exec-id", exec_id]
    for chave, valor in kwargs.items():
        flag = "--" + chave.replace("_", "-")
        if isinstance(valor, bool):
            if valor:
                cmd.append(flag)
        else:
            cmd.extend([flag, str(valor)])
    return subprocess.Popen(cmd, cwd=RAIZ)


def _encerrar(processo, nome=""):
    """Derruba um processo-no (usado tanto para simular falha quanto para
    encerrar tudo no fim do teste). Tenta terminate(); se nao responder a
    tempo, forca com kill()."""
    if processo.poll() is None:
        processo.terminate()
        try:
            processo.wait(timeout=5)
        except subprocess.TimeoutExpired:
            processo.kill()
            processo.wait()


def _cor(texto, codigo):
    """Aplica um codigo de cor ANSI ao texto, so quando a saida e um
    terminal (evita imprimir codigos de escape quando redirecionado a um arquivo)."""
    if not sys.stdout.isatty():
        return texto
    return f"\033[{codigo}m{texto}\033[0m"


def _rotulo_no(nome, nos_do_teste, nos_caidos):
    """Formata o nome de um no para o desenho da topologia: [-] se nao faz
    parte do teste, [X] (vermelho) se foi derrubado, [OK] (verde) caso ativo."""
    if nome not in nos_do_teste:
        return _cor(f"{nome}[-]", "90")
    if nome in nos_caidos:
        return _cor(f"{nome}[X]", "31")
    return _cor(f"{nome}[OK]", "32")


def desenhar_topologia(titulo, nos_do_teste, nos_caidos=()):
    """Imprime no terminal os dois caminhos da rede com o estado atual de
    cada no — chamado no inicio do teste, quando um no e derrubado e no final."""
    nos_caidos = set(nos_caidos)
    r = lambda nome: _rotulo_no(nome, nos_do_teste, nos_caidos)
    print(f"\n=== {titulo} ===")
    print(f"  Caminho superior : {r('S')} -> {r('R1')} -> {r('R2')} -> {r('C')}")
    print(f"  Caminho inferior : {r('S')} -> {r('R3')} -> {r('R4')} -> {r('C')}")
    print("  Legenda: [OK] ativo   [X] derrubado   [-] nao usado neste teste")


def calcular_resumo(nome_teste):
    """Le todos os logs JSON Lines de um teste e soma as contagens usadas na
    conferencia da secao 9/10 do enunciado (leituras geradas, entregues,
    duplicatas, descartes por TTL e envios totais na rede)."""
    geradas = unicas = duplicatas = descarte_ttl = envios = 0
    for caminho in glob.glob(os.path.join(RAIZ, "logs", nome_teste, "*.jsonl")):
        with open(caminho, encoding="utf-8") as arquivo:
            for linha in arquivo:
                acao = json.loads(linha)["acao"]
                if acao == "leitura_gerada":
                    geradas += 1
                elif acao == "entrega_unica":
                    unicas += 1
                elif acao == "duplicata_recebida":
                    duplicatas += 1
                elif acao == "descarte_ttl":
                    descarte_ttl += 1
                elif acao == "envio_aceito":
                    envios += 1
    taxa = (100 * unicas / geradas) if geradas else 0.0
    return {"geradas": geradas, "unicas": unicas, "duplicatas": duplicatas,
            "descarte_ttl": descarte_ttl, "envios": envios, "taxa": taxa}


def imprimir_resumo(nome_teste, resumo, nos_caidos):
    """Imprime o bloco 'Simulacoes realizadas' com os numeros de calcular_resumo."""
    print(f"\n--- Simulacoes realizadas: {nome_teste} ---")
    if nos_caidos:
        print(f"  No(s) derrubado(s)     : {', '.join(nos_caidos)}")
    else:
        print("  No(s) derrubado(s)     : nenhum (execucao sem falhas)")
    print(f"  Leituras geradas       : {resumo['geradas']}")
    print(f"  Entregues (unicas)     : {resumo['unicas']}")
    print(f"  Duplicatas em C        : {resumo['duplicatas']}")
    print(f"  Descartadas por TTL    : {resumo['descarte_ttl']}")
    print(f"  Envios totais na rede  : {resumo['envios']}")
    print(f"  Taxa de entrega        : {resumo['taxa']:.1f}%")


def rodar_teste_padrao(nome_teste):
    """Executa um dos testes T1-T6: sobe os receptores, roda o sensor pela
    primeira metade das leituras, derruba o(s) no(s) configurado(s) (se
    houver), roda o sensor de novo a partir da leitura seguinte, encerra
    tudo e imprime o desenho da topologia + resumo final."""
    cfg = TESTES[nome_teste]
    nos_do_teste = set(cfg["nos"]) | {"S"}
    processos = {}

    desenhar_topologia(f"Teste {nome_teste} - iniciando", nos_do_teste)

    for no in cfg["nos"]:
        processos[no] = iniciar_no(no, nome_teste)
    time.sleep(1)  # da tempo dos receptores subirem antes do sensor

    sensor1 = iniciar_no("S", nome_teste, sequencia_inicial=1,
                         quantidade=cfg["quantidade"], ttl_inicial=cfg["ttl_inicial"])
    sensor1.wait()

    nos_caidos = []
    if cfg["derrubar"]:
        time.sleep(5)  # aguarda o processamento das mensagens em transito
        for no in cfg["derrubar"]:
            _encerrar(processos[no], no)
            nos_caidos.append(no)
        desenhar_topologia(f"Teste {nome_teste} - falha simulada", nos_do_teste, nos_caidos)

        sensor2 = iniciar_no("S", nome_teste, sequencia_inicial=cfg["quantidade"] + 1,
                             quantidade=cfg["quantidade"], ttl_inicial=cfg["ttl_inicial"])
        sensor2.wait()

    time.sleep(5)
    for no, processo in processos.items():
        _encerrar(processo, no)

    desenhar_topologia(f"Teste {nome_teste} - concluido", nos_do_teste, nos_caidos)
    resumo = calcular_resumo(nome_teste)
    imprimir_resumo(nome_teste, resumo, nos_caidos)
    print(f"\n[{nome_teste}] logs em logs/{nome_teste}/, CSV em resultados/leituras_{nome_teste}.csv\n")

    return resumo, nos_caidos


def rodar_t7():
    """Executa o teste T7: so S, R1, R2 e C; o sensor manda duas copias
    identicas de uma unica leitura (duplicar_leitura=True), confirmando que
    R1 encaminha uma e descarta a outra como duplicata."""
    nome_teste = "T7"
    nos_do_teste = {"S", "R1", "R2", "C"}

    desenhar_topologia(f"Teste {nome_teste} - iniciando", nos_do_teste)

    processos = {no: iniciar_no(no, nome_teste) for no in ("C", "R2", "R1")}
    time.sleep(1)

    sensor = iniciar_no("S", nome_teste, sequencia_inicial=1, quantidade=1,
                        ttl_inicial=3, duplicar_leitura=True)
    sensor.wait()

    time.sleep(5)
    for no, processo in processos.items():
        _encerrar(processo, no)

    desenhar_topologia(f"Teste {nome_teste} - concluido", nos_do_teste)
    resumo = calcular_resumo(nome_teste)
    imprimir_resumo(nome_teste, resumo, [])
    print(f"\n[{nome_teste}] logs em logs/{nome_teste}/, CSV em resultados/leituras_{nome_teste}.csv\n")

    return resumo, []


def rodar_todos():
    """Roda T1 a T7 em sequencia e imprime uma tabela comparativa com o
    resumo de todos os testes ao final."""
    resumos = {}
    nos_caidos_por_teste = {}

    for nome_teste in ORDEM_TESTES:
        if nome_teste == "T7":
            resumo, nos_caidos = rodar_t7()
        else:
            resumo, nos_caidos = rodar_teste_padrao(nome_teste)
        resumos[nome_teste] = resumo
        nos_caidos_por_teste[nome_teste] = nos_caidos

    print("\n================ RESUMO COMPARATIVO (T1-T7) ================")
    print(f"{'Teste':<6}{'Geradas':>9}{'Unicas':>9}{'Duplic.':>9}{'DescTTL':>9}{'Envios':>9}{'Taxa':>8}")
    for nome_teste in ORDEM_TESTES:
        r = resumos[nome_teste]
        print(f"{nome_teste:<6}{r['geradas']:>9}{r['unicas']:>9}{r['duplicatas']:>9}"
              f"{r['descarte_ttl']:>9}{r['envios']:>9}{r['taxa']:>7.1f}%")
        if nos_caidos_por_teste[nome_teste]:
            print(f"       (no(s) derrubado(s): {', '.join(nos_caidos_por_teste[nome_teste])})")
    print("==============================================================\n")


def main():
    """Interpreta --teste (um unico teste) ou --todos (T1 a T7 em sequencia)."""
    parser = argparse.ArgumentParser(description="Orquestra a execucao dos testes T1-T7.")
    grupo = parser.add_mutually_exclusive_group(required=True)
    grupo.add_argument("--teste", choices=ORDEM_TESTES)
    grupo.add_argument("--todos", action="store_true", help="roda T1 a T7 em sequencia")
    args = parser.parse_args()

    if args.todos:
        rodar_todos()
    elif args.teste == "T7":
        rodar_t7()
    else:
        rodar_teste_padrao(args.teste)


if __name__ == "__main__":
    main()

"""
Ponto de entrada por linha de comando: executa UM no da rede (S, R1-R4 ou C).

Uso tipico (um processo por terminal, coletor e retransmissores primeiro):
    python executar_no.py --no C --exec-id teste1
    python executar_no.py --no R1 --exec-id teste1
    python executar_no.py --no S --exec-id teste1 --quantidade 10

O orquestrador (orquestrador.py) chama este script varias vezes como
subprocessos, um por no, para automatizar os testes T1-T7.
"""
import argparse

from protocolo.no import carregar_topologia, rodar_coletor, rodar_retransmissor, rodar_sensor

NOS_RETRANSMISSORES = {"R1", "R2", "R3", "R4"}


def main():
    """Le os argumentos da linha de comando e despacha para o papel certo
    (rodar_sensor / rodar_retransmissor / rodar_coletor)."""
    parser = argparse.ArgumentParser(
        description="Executa um unico no da rede PRN (S, R1-R4 ou C)."
    )
    parser.add_argument("--no", required=True, choices=["S", "R1", "R2", "R3", "R4", "C"])
    parser.add_argument("--exec-id", required=True, dest="exec_id",
                         help="Identificador da execucao/teste (ex: T2)")
    parser.add_argument("--config", default="config/topologia.json")
    # Opcoes abaixo so fazem sentido para o Sensor (--no S); os outros papeis as ignoram.
    parser.add_argument("--sequencia-inicial", type=int, default=1, dest="sequencia_inicial")
    parser.add_argument("--quantidade", type=int, default=10)
    parser.add_argument("--intervalo", type=float, default=2.0)
    parser.add_argument("--ttl-inicial", type=int, default=3, dest="ttl_inicial")
    parser.add_argument("--duplicar-leitura", action="store_true", dest="duplicar_leitura")
    args = parser.parse_args()

    topologia = carregar_topologia(args.config)

    if args.no == "S":
        rodar_sensor(
            topologia,
            args.exec_id,
            sequencia_inicial=args.sequencia_inicial,
            quantidade=args.quantidade,
            intervalo=args.intervalo,
            ttl_inicial=args.ttl_inicial,
            duplicar_leitura=args.duplicar_leitura,
        )
    elif args.no in NOS_RETRANSMISSORES:
        rodar_retransmissor(args.no, topologia, args.exec_id)
    else:
        rodar_coletor(topologia, args.exec_id)


if __name__ == "__main__":
    main()

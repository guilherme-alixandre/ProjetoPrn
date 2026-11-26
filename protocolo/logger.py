"""
Logging de eventos da rede em formato JSON Lines (um evento por linha).

Cada no (S, R1-R4 ou C) tem seu proprio arquivo logs/<exec_id>/<no>.jsonl,
com os campos exigidos pela secao 9 do enunciado (execucao, horario, no,
origem, sequencia, remetente do salto, proximo no, ttl, acao) mais um campo
livre 'detalhe'. O conjunto ACOES funciona como uma lista fechada de eventos
validos, evitando strings soltas e inconsistentes espalhadas pelo codigo.
"""
import json
import os
from datetime import datetime, timezone

ACOES = {
    "leitura_gerada",
    "tentativa_envio",
    "envio_aceito",
    "erro_envio",
    "recepcao",
    "encaminhado",
    "descarte_ttl",
    "descarte_duplicata",
    "descarte_remetente",
    "descarte_formato",
    "erro_integridade",
    "erro_recepcao",
    "descarte_expirado",
    "pulso_enviado",
    "pulso_recebido",
    "vizinho_inativo",
    "vizinho_reativado",
    "entrega_unica",
    "duplicata_recebida",
}


class LoggerJSONL:
    """Grava um evento por linha (JSON Lines) em logs/<exec_id>/<no>.jsonl."""

    def __init__(self, no, exec_id, pasta_logs="logs"):
        """Abre (criando se preciso) o arquivo de log deste no para esta execucao/teste."""
        self.no = no
        self.exec_id = exec_id
        pasta = os.path.join(pasta_logs, exec_id)
        os.makedirs(pasta, exist_ok=True)
        caminho = os.path.join(pasta, f"{no}.jsonl")
        self._arquivo = open(caminho, "a", encoding="utf-8")

    def registrar(self, acao, origem=None, sequencia=None, remetente_salto=None,
                  proximo_no=None, ttl=None, detalhe=None):
        """Grava um evento. 'acao' deve estar em ACOES; os demais campos sao
        preenchidos conforme fizerem sentido para o evento (ex: 'recepcao' tem
        remetente_salto, 'tentativa_envio' tem proximo_no)."""
        if acao not in ACOES:
            raise ValueError(f"acao desconhecida: {acao}")
        evento = {
            "execucao": self.exec_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "no": self.no,
            "origem": origem,
            "sequencia": sequencia,
            "remetente_salto": remetente_salto,
            "proximo_no": proximo_no,
            "ttl": ttl,
            "acao": acao,
            "detalhe": detalhe,
        }
        # flush imediato: cada linha de log deve sobreviver mesmo se o processo
        # for derrubado abruptamente logo em seguida (testes de falha T3-T5).
        self._arquivo.write(json.dumps(evento, ensure_ascii=False) + "\n")
        self._arquivo.flush()

    def fechar(self):
        """Fecha o arquivo de log deste no."""
        self._arquivo.close()

# Rede PRN com retransmissão (4 retransmissores, 2 caminhos)

Trabalho de Redes Móveis — IFG Câmpus Inhumas, 2026/2. Emulação de uma rede de pacotes (Packet Radio
Network) em que um sensor `S` envia leituras fictícias de temperatura até um coletor `C`, passando por
dois caminhos redundantes de dois retransmissores cada (`S → R1 → R2 → C` e `S → R3 → R4 → C`).

```
      R1 ──── R2
     ╱            ╲
   S                C
     ╲            ╱
      R3 ──── R4
```

## Requisitos

- Python 3.10+ (desenvolvido e testado em 3.14.7).
- Nenhuma dependência externa — só biblioteca padrão (`socket`, `json`, `csv`, `hmac`, `hashlib`,
  `logging`-style JSON Lines próprio, `threading` não é usado). Ver `requirements.txt`.
- Windows, Linux ou macOS (os 6 processos rodam na mesma máquina, todos em `127.0.0.1`).

## Instalação

```bash
git clone <https://github.com/guilherme-alixandre/ProjetoPrn.git>
cd ProjetoPrn
python -m venv .venv
# Windows: .venv\Scripts\activate | Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt   # nao instala nada, so documenta a ausencia de deps
```

## Estrutura do repositório

```
config/topologia.json   # portas, vizinhos permitidos e parametros do protocolo (chave HMAC, etc.)
protocolo/
  pacote.py             # formato do pacote, HMAC, validacao, TTL duplo (saltos + idade)
  logger.py             # logging em JSON Lines (1 evento por linha)
  no.py                 # logica de Sensor, Retransmissor (R1-R4) e Coletor
executar_no.py           # CLI para rodar UM no (S, R1-R4 ou C)
orquestrador.py           # sobe/derruba os 6 processos automaticamente para cada teste T1-T7
logs/<exec_id>/<no>.jsonl       # log de eventos por no, por execucao/teste
resultados/leituras_<exec_id>.csv  # leituras entregues no coletor, por execucao/teste
```

## Formato do pacote

```json
{
  "tipo": "leitura",
  "origem": "S", "destino": "C", "sequencia": 42, "ttl": 2, "temperatura_c": 27.4,
  "criado_em": 1764174900.62,
  "hmac": "9f2a71c4e8b0d5a3"
}
```

- `ttl`: limite de saltos (decrementado a cada retransmissão; descarte em 0 nos retransmissores, aceito
  em 0 no coletor).
- `criado_em`: timestamp de geração (não muda nos saltos); retransmissores descartam pacotes mais velhos
  que `parametros.tempo_de_vida_maximo` (TTL temporal, além do TTL de saltos).
- `hmac`: `HMAC-SHA256` com a chave fixa em `config/topologia.json → parametros.chave_hmac`, recalculado a
  cada salto (o `ttl` muda). Detecta corrupção e autentica a origem do pacote sem handshake de troca de
  chave.
- Pacotes de controle (`tipo: "pulso"`) trafegam na mesma porta, usados só para detecção de vizinho
  inativo — não contam nas estatísticas de leituras/entregas.

## Executando um nó manualmente

```bash
# terminal 1 (coletor primeiro)
python executar_no.py --no C --exec-id manual1

# terminal 2
python executar_no.py --no R2 --exec-id manual1

# terminal 3
python executar_no.py --no R1 --exec-id manual1

# terminal 4 — sensor por último, gera 5 leituras com intervalo de 2s
python executar_no.py --no S --exec-id manual1 --quantidade 5 --intervalo 2 --ttl-inicial 3
```

Opções do Sensor: `--sequencia-inicial`, `--quantidade`, `--intervalo`, `--ttl-inicial`,
`--duplicar-leitura` (envia duas cópias idênticas, usado no T7).

## Executando os testes automatizados (recomendado)

O orquestrador sobe os processos necessários, roda o sensor, e nos testes de falha derruba o(s) nó(s)
indicado(s) no meio da execução:

```bash
python orquestrador.py --teste T1
python orquestrador.py --teste T2
python orquestrador.py --teste T3
python orquestrador.py --teste T4
python orquestrador.py --teste T5
python orquestrador.py --teste T6
python orquestrador.py --teste T7

# ou rodar T1 a T7 em sequencia com um resumo comparativo no final
python orquestrador.py --todos
```

O orquestrador desenha no terminal a topologia (caminho superior e inferior) no início de cada teste, de
novo quando um nó é derrubado (marcado `[X]`) e no final, seguido de um resumo com leituras geradas,
entregues, duplicatas, descartes por TTL, envios totais e taxa de entrega.

| Teste | Configuração | Evidência esperada |
|---|---|---|
| T1 | Só o caminho superior (S, R1, R2, C), 10 leituras | Entrega pelos 3 saltos do caminho superior |
| T2 | Os 6 nós, 10 leituras | 10 entregas únicas + 10 duplicatas em C; 60 envios totais na rede |
| T3 | Todos, derruba R1 após a leitura 10 | Leituras 11–20 só pelo caminho R3→R4 |
| T4 | Todos, derruba R2 após a leitura 10 | R1 tenta enviar a R2; caminho R3→R4 mantém 100% de entrega |
| T5 | Todos, derruba R2 e R4 após a leitura 10 | Leituras 11–20 não chegam a C |
| T6 | Todos, TTL inicial = 2 | R2 e R4 recebem TTL 0 e descartam antes de alcançar C |
| T7 | Só S, R1, R2, C; sensor manda 2 cópias idênticas de 1 leitura | R1 encaminha 1, descarta 1 duplicata, C recebe 1 entrega |

Cada execução grava logs em `logs/<teste>/<no>.jsonl` (um evento JSON por linha, com execução, horário,
nó, origem, sequência, remetente do salto, próximo nó, TTL e ação) e o coletor grava
`resultados/leituras_<teste>.csv` com as leituras entregues.

## Mecanismos de confiabilidade implementados

Além do TTL de saltos, deduplicação e validação de remetente/formato exigidos pelo enunciado, o protocolo
acrescenta (parâmetros configuráveis em `config/topologia.json → parametros`):

1. **HMAC por salto** — autenticidade + integridade (não só detecção de corrupção).
2. **TTL duplo** — limite de saltos + limite de idade (`tempo_de_vida_maximo`).
3. **Janela de deduplicação com expiração** (`janela_dedup_segundos`) — a memória de IDs já encaminhados
   não cresce indefinidamente.
4. **Pulso de vizinhança** (`intervalo_pulso`, `tolerancia_pulsos_perdidos`) — cada nó avisa o próximo que
   está vivo; quem recebe detecta ausência prolongada (`vizinho_inativo`/`vizinho_reativado` nos logs),
   sem alterar as contagens de envio de dados.

## Teste extra T8 (fora do PDF): pacotes com informações erradas

Além dos testes T1–T7 exigidos no enunciado, `teste_t8.py` fabrica pacotes inválidos na mão (sem passar
pelo Sensor normal) e confirma que R1 descarta cada um pelo motivo certo, sem nada chegar ao coletor:

```bash
python teste_t8.py
```

Casos cobertos: destino errado (`descarte_formato`), HMAC adulterado (`erro_integridade`), TTL negativo
(`descarte_formato`) e remetente não autorizado — porta errada (`descarte_remetente`). O script já imprime
PASSOU/FALHOU para cada caso; os logs completos ficam em `logs/T8/`.

## Limitações da emulação

- Todos os 6 processos rodam na mesma máquina, em `127.0.0.1` — alcance de rádio e fenômenos físicos não
  são simulados.
- Topologia e vizinhança são fixas, configuradas em `config/topologia.json`; não há descoberta de rota.
- O cenário básico não usa confirmação (ACK) nem retransmissão automática de pacotes de dados — um envio
  UDP aceito pelo socket não comprova a chegada ao próximo nó.

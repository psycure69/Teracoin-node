import hashlib
import time
import random
import socket
import threading
import json
import os
from datetime import datetime

# Lê a porta dinâmica fornecida pelo Railway (ou usa 5000 como padrão local)
PORTA_LOCAL = int(os.environ.get("PORT", 5000))

TEMPO_ALVO_MIN = 120.0  # 2 minutos
TEMPO_ALVO_MAX = 300.0  # 5 minutos
DIFICULDADE_MINIMA = 3
INTERVALO_CICLO_SERVIDOR = 5  # segundos entre ciclos no modo contínuo

RECOMPENSA_INICIAL = 50.0
INTERVALO_HALVING_EMISSAO = 10_000_000.0  # reduz pela metade a cada 10 milhões de TRC
EMISSAO_MAXIMA = 50_000_000.0  # teto máximo de 50 milhões de TRC


def log(mensagem):
    agora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{agora}] {mensagem}", flush=True)


class Carteira:
    def __init__(self, chave_personalizada=None):
        self.chave_privada = (
            chave_personalizada
            if chave_personalizada
            else str(random.randint(1000000000, 9999999999))
        )
        self.endereco = hashlib.sha256(self.chave_privada.encode()).hexdigest()[:20]

    def assinar_transacao(self, dados_transacao):
        mensagem = dados_transacao + self.chave_privada
        return hashlib.sha256(mensagem.encode()).hexdigest()


def verificar_assinatura(endereco_publico, dados_transacao, assinatura, chave_privada_teste):
    verif_endereco = hashlib.sha256(chave_privada_teste.encode()).hexdigest()[:20]
    mensagem = dados_transacao + chave_privada_teste
    verif_assinatura = hashlib.sha256(mensagem.encode()).hexdigest()
    return verif_endereco == endereco_publico and verif_assinatura == assinatura


class Bloco:
    def __init__(self, indice, transacoes, timestamp, hash_anterior):
        self.indice = indice
        self.transacoes = transacoes
        self.timestamp = timestamp
        self.hash_anterior = hash_anterior
        self.nonce = 0
        self.hash = self.calcular_hash()

    def calcular_hash(self):
        dados_bloco = f"{self.indice}{str(self.transacoes)}{self.timestamp}{self.hash_anterior}{self.nonce}"
        return hashlib.sha256(dados_bloco.encode()).hexdigest()

    def minerar_bloco(self, dificuldade):
        alvo = "0" * dificuldade
        log(f"[Teracoin] A minerar o Bloco #{self.indice} (alvo: {alvo}...)...")
        inicio = time.time()
        while self.hash[:dificuldade] != alvo:
            self.nonce += 1
            self.hash = self.calcular_hash()
        duracao = time.time() - inicio
        log(f"[Sucesso] Bloco #{self.indice} minerado! Hash: {self.hash}")
        log(f"[Teracoin] Nonce: {self.nonce} | Tempo: {duracao:.2f}s")
        return duracao


class TeracoinRede:
    def __init__(self, dificuldade_inicial=4):
        self.cadeia = []
        self.transacoes_pendentes = []
        self.dificuldade = max(DIFICULDADE_MINIMA, dificuldade_inicial)
        self.conexoes_conhecidas = []
        self.lock = threading.Lock()
        self.criar_bloco_genesis()

    def criar_bloco_genesis(self):
        bloco_g = Bloco(
            0,
            [{"tipo": "GENESIS", "detalhes": "Lançamento oficial da rede Teracoin"}],
            time.time(),
            "0",
        )
        self.cadeia.append(bloco_g)

    def obter_ultimo_bloco(self):
        return self.cadeia[-1]

    def total_emitido(self):
        emitido = 0.0
        for bloco in self.cadeia:
            for tx in bloco.transacoes:
                if tx.get("tipo") == "RECOMPENSA":
                    emitido += float(tx.get("valor", 0.0))
        return emitido

    def calcular_recompensa_atual(self):
        emitido = self.total_emitido()
        restante_teto = max(0.0, EMISSAO_MAXIMA - emitido)
        if restante_teto <= 0.0:
            return 0.0

        faixas_concluidas = int(emitido // INTERVALO_HALVING_EMISSAO)
        recompensa = RECOMPENSA_INICIAL / (2 ** faixas_concluidas)
        return round(min(recompensa, restante_teto), 8)

    def ajustar_dificuldade(self, tempo_gasto):
        if tempo_gasto < 10.0:
            self.dificuldade += 2
            log(
                f"[Dificuldade] Bloco muito rápido ({tempo_gasto:.2f}s). "
                f"Subiu +2 -> Nova dificuldade: {self.dificuldade}"
            )
        elif tempo_gasto < TEMPO_ALVO_MIN:
            self.dificuldade += 1
            log(
                f"[Dificuldade] Abaixo de 2 min ({tempo_gasto:.2f}s). "
                f"Subiu +1 -> Nova dificuldade: {self.dificuldade}"
            )
        elif tempo_gasto > TEMPO_ALVO_MAX and self.dificuldade > DIFICULDADE_MINIMA:
            self.dificuldade -= 1
            log(
                f"[Dificuldade] Acima de 5 min ({tempo_gasto:.2f}s). "
                f"Desceu -1 -> Nova dificuldade: {self.dificuldade}"
            )
        else:
            log(
                f"[Dificuldade] Dentro da meta de 2–5 min ({tempo_gasto:.2f}s). "
                f"Dificuldade mantida em {self.dificuldade}"
            )

    def calcular_saldo(self, endereco_carteira):
        with self.lock:
            saldo = 0.0
            for bloco in self.cadeia:
                for tx in bloco.transacoes:
                    if tx.get("tipo") == "RECOMPENSA" and tx.get("minerador") == endereco_carteira:
                        saldo += float(tx.get("valor", 0.0))
                    if tx.get("remetente") == endereco_carteira:
                        saldo -= float(tx.get("valor", 0.0))
                    if tx.get("destinatario") == endereco_carteira:
                        saldo += float(tx.get("valor", 0.0))
            return round(saldo, 8)

    def saldo_disponivel_para_envio(self, endereco_carteira):
        saldo = self.calcular_saldo(endereco_carteira)
        with self.lock:
            for tx in self.transacoes_pendentes:
                if tx.get("remetente") == endereco_carteira:
                    saldo -= float(tx.get("valor", 0.0))
        return round(saldo, 8)

    def criar_transacao(self, carteira_remetente, endereco_destinatario, valor):
        if valor <= 0:
            log("[Erro] O valor da transferência deve ser maior que zero.")
            return False

        if endereco_destinatario == carteira_remetente.endereco:
            log("[Erro] Não é possível enviar TRC para a mesma carteira.")
            return False

        disponivel = self.saldo_disponivel_para_envio(carteira_remetente.endereco)
        if disponivel < valor:
            log(
                f"[Rejeitada] Saldo insuficiente! Disponível: {disponivel} TRC | Tentativa: {valor} TRC"
            )
            return False

        payload = f"{carteira_remetente.endereco}->{endereco_destinatario}:{valor}"
        assinatura = carteira_remetente.assinar_transacao(payload)

        if not verificar_assinatura(
            carteira_remetente.endereco,
            payload,
            assinatura,
            carteira_remetente.chave_privada,
        ):
            log("[Rejeitada] Assinatura inválida.")
            return False

        tx = {
            "tipo": "TRANSFERENCIA",
            "remetente": carteira_remetente.endereco,
            "destinatario": endereco_destinatario,
            "valor": valor,
            "payload": payload,
            "assinatura": assinatura,
        }
        with self.lock:
            self.transacoes_pendentes.append(tx)
        log(f"[Mempool] Transação adicionada à fila: {valor} TRC para {endereco_destinatario[:10]}...")
        return True

    def minerar_bloco_com_recompensa(self, endereco_minerador):
        with self.lock:
            recompensa_atual = self.calcular_recompensa_atual()
            transacoes_do_bloco = list(self.transacoes_pendentes)
            if recompensa_atual > 0:
                transacoes_do_bloco.append(
                    {
                        "tipo": "RECOMPENSA",
                        "minerador": endereco_minerador,
                        "valor": recompensa_atual,
                    }
                )
            else:
                log("[Emissão] Teto de 50.000.000 TRC atingido. Bloco sem emissão de novas moedas.")

            ultimo_bloco = self.obter_ultimo_bloco()
            indice_novo = len(self.cadeia)
            hash_ant = ultimo_bloco.hash
            dif_atual = self.dificuldade

        novo_bloco = Bloco(
            indice=indice_novo,
            transacoes=transacoes_do_bloco,
            timestamp=time.time(),
            hash_anterior=hash_ant,
        )

        tempo_gasto = novo_bloco.minerar_bloco(dif_atual)

        with self.lock:
            if self.obter_ultimo_bloco().hash == hash_ant and novo_bloco.hash.startswith(
                "0" * self.dificuldade
            ):
                self.cadeia.append(novo_bloco)
                txs_incluidas = [t for t in transacoes_do_bloco if t.get("tipo") == "TRANSFERENCIA"]
                for tx_inc in txs_incluidas:
                    if tx_inc in self.transacoes_pendentes:
                        self.transacoes_pendentes.remove(tx_inc)
                self.ajustar_dificuldade(tempo_gasto)
                bloco_para_enviar = novo_bloco
            else:
                log("[Teracoin] Bloco descartado: a cadeia avançou durante a mineração.")
                return

        self.transmitir_bloco(bloco_para_enviar)

    def transmitir_bloco(self, bloco):
        dados_bloco = json.dumps(
            {
                "indice": bloco.indice,
                "transacoes": bloco.transacoes,
                "timestamp": bloco.timestamp,
                "hash_anterior": bloco.hash_anterior,
                "nonce": bloco.nonce,
                "hash": bloco.hash,
            }
        )

        for ip in self.conexoes_conhecidas:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(5)
                s.connect((ip, PORTA_LOCAL))
                s.send(dados_bloco.encode("utf-8"))
                s.close()
                log(f"[P2P] Bloco enviado para o nó {ip}")
            except Exception as e:
                log(f"[Erro P2P] Falha ao enviar para {ip}: {e}")

    def adicionar_bloco_recebido(self, novo_bloco_dict):
        bloco = Bloco(
            novo_bloco_dict["indice"],
            novo_bloco_dict["transacoes"],
            novo_bloco_dict["timestamp"],
            novo_bloco_dict["hash_anterior"],
        )
        bloco.nonce = novo_bloco_dict["nonce"]
        bloco.hash = novo_bloco_dict["hash"]

        with self.lock:
            ultimo_bloco = self.obter_ultimo_bloco()
            if bloco.hash_anterior == ultimo_bloco.hash and bloco.hash.startswith(
                "0" * self.dificuldade
            ):
                self.cadeia.append(bloco)
                log(f"[Rede Teracoin] Bloco #{bloco.indice} sincronizado com sucesso!")
                return True
            log("[Rede Teracoin] Bloco rejeitado por falha de validação.")
            return False


def iniciar_servidor_p2p(rede_teracoin):
    servidor = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    servidor.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    servidor.bind(("0.0.0.0", PORTA_LOCAL))
    servidor.listen(5)
    log(f"[Servidor Teracoin] A escutar na porta {PORTA_LOCAL}...")

    while True:
        cliente_socket, _ = servidor.accept()
        try:
            dados = cliente_socket.recv(4096).decode("utf-8")
            if dados:
                bloco_dict = json.loads(dados)
                rede_teracoin.adicionar_bloco_recebido(bloco_dict)
        except Exception as e:
            log(f"[Erro P2P] Falha ao processar dados recebidos: {e}")
        finally:
            cliente_socket.close()


if __name__ == "__main__":
    log("=== REDE TERACOIN (TRC) - MODO RAILWAY (META 2 A 5 MIN) ===")

    teracoin_rede = TeracoinRede(dificuldade_inicial=4)

    thread_p2p = threading.Thread(
        target=iniciar_servidor_p2p, args=(teracoin_rede,), daemon=True
    )
    thread_p2p.start()

    carteira_criador = Carteira(chave_personalizada="chave_mestra_criador_teracoin")
    log(f"[Carteira Criador] Endereço ativo: {carteira_criador.endereco}")

    try:
        while True:
            teracoin_rede.minerar_bloco_com_recompensa(carteira_criador.endereco)
            saldo_atual = teracoin_rede.calcular_saldo(carteira_criador.endereco)
            emitido_total = teracoin_rede.total_emitido()
            prox_recompensa = teracoin_rede.calcular_recompensa_atual()
            log(
                f"[Status] Blocos: {len(teracoin_rede.cadeia)} | "
                f"Emitido: {emitido_total}/{EMISSAO_MAXIMA:.0f} TRC | "
                f"Próx. recompensa: {prox_recompensa} TRC | "
                f"Saldo Criador: {saldo_atual} TRC | "
                f"Dificuldade: {teracoin_rede.dificuldade}"
            )
            time.sleep(INTERVALO_CICLO_SERVIDOR)
    except KeyboardInterrupt:
        log("[Teracoin] Encerramento solicitado. Até logo!")

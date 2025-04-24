#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
AVISO: Este ficheiro contém placeholders para credenciais e configurações sensíveis.
Antes de usar em produção, substitua os seguintes valores:
- DB_Server, DB_User, DB_Password, DB_DB na classe Contador
- Lista allowed_origins para CORS
- Ficheiros CERT.key e CERT.crt por certificados válidos

Veja o README.md para mais informações sobre a configuração.
"""

import sys
import os
import time
import logging
import signal
import threading
import traceback
import atexit
import numpy as np
from datetime import datetime, timedelta
from functools import wraps
import ssl
import pymssql
import math
import RPi.GPIO as GPIO  # Usar apenas RPi.GPIO
from flask import Flask, jsonify, request, make_response
from queue import Queue

# Configuração robusta do logging primeiro, antes de qualquer uso
logging.basicConfig(
    filename="app.log",
    level=logging.INFO,
    format="%(asctime)s;%(levelname)s;%(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# Configura um logger para a consola também
console = logging.StreamHandler()
console.setLevel(logging.INFO)
formatter = logging.Formatter("%(asctime)s;%(levelname)s;%(message)s")
console.setFormatter(formatter)
logging.getLogger("").addHandler(console)

# Configuração do GPIO
GPIO.setwarnings(False)  # Desativa avisos
GPIO.setmode(GPIO.BCM)   # Usar numeração BCM

# Cria uma fila para operações de BD para evitar bloqueios
db_queue = Queue()

# Decorador para capturar e registar exceções
def log_exceptions(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            logging.error(f"Exceção em {func.__name__}: {str(e)}")
            logging.error(traceback.format_exc())
            if isinstance(e, pymssql.Error):
                logging.error(f"Erro de BD em {func.__name__}: {str(e)}")
            return None
    return wrapper

# Função segura para conexão à BD
def get_db_connection(db_server, db_user, db_password, db_name, max_retries=3):
    retries = 0
    while retries < max_retries:
        try:
            conn = pymssql.connect(db_server, db_user, db_password, db_name, timeout=10)
            return conn
        except pymssql.Error as e:
            retries += 1
            logging.error(f"Falha na conexão BD (tentativa {retries}): {str(e)}")
            if retries >= max_retries:
                logging.error("Excedido número máximo de tentativas de conexão à BD")
                raise
            time.sleep(2)  # Espera antes de tentar novamente

app = Flask(__name__)

# Middleware para adicionar cabeçalhos CORS a todas as respostas
@app.after_request
def add_cors_headers(response):
    allowed_origins = ["http://localhost:3000", "https://example.com"]
    origin = request.headers.get("Origin")
    
    if origin in allowed_origins:
        response.headers.add("Access-Control-Allow-Origin", origin)
        response.headers.add("Access-Control-Allow-Headers", "Content-Type,Authorization")
        response.headers.add("Access-Control-Allow-Methods", "GET,POST,PUT,DELETE,OPTIONS")
        response.headers.add("Access-Control-Allow-Credentials", "true")
    
    return response

# Rota OPTIONS para responder a preflight CORS
@app.route('/', defaults={'path': ''}, methods=['OPTIONS'])
@app.route('/<path:path>', methods=['OPTIONS'])
def handle_options(path):
    response = make_response()
    allowed_origins = ["http://localhost:3000", "https://example.com"]
    origin = request.headers.get("Origin")
    
    if origin in allowed_origins:
        response.headers.add("Access-Control-Allow-Origin", origin)
        response.headers.add("Access-Control-Allow-Headers", "Content-Type,Authorization")
        response.headers.add("Access-Control-Allow-Methods", "GET,POST,PUT,DELETE,OPTIONS")
        response.headers.add("Access-Control-Allow-Credentials", "true")
    
    return response

class Contador:
    def __init__(self):
        # Configuração de pinos GPIO com proteção
        self.SENSOR_PIN = 22  # Pino do sensor de contagem
        self.DOOR_PIN = 23    # Pino de controlo da porta

        # Estado do pino de entrada - uso do pull-up interno
        self.pullup = True
        self.invert_logic = False  # Se True, inverte a lógica de deteção do sensor
        
        # Variáveis para controlo da leitura do sensor
        self.previous_sensor_state = None
        self.using_polling_only = True  # Flag para indicar uso exclusivo de polling
        self.sensor_initialized = False
        self.door_initialized = False
        self.last_transition_time = 0  # Timestamp da última transição
        self.debug_counter = 0  # Contador para logs de depuração limitados
        
        # Variáveis para controlo de erros e recuperação
        self._state_lock = threading.RLock()
        self._contagem_lock = threading.RLock()
        
        self.sensor_last_reset = time.time()
        self.sensor_reset_attempts = 0
        self.max_reset_attempts = 5  # Máximo de tentativas de reset
        self.reset_cooldown = 60  # Tempo de espera entre resets (segundos)
        
        # Configuração da BD
        self.DB_Server = "your_db_server"
        self.DB_User = "your_db_user"
        self.DB_Password = "your_db_password"
        self.DB_DB = "your_db_name"
        
        # Estados:
        # 0: Parado
        # 1: Contagem
        # 2: Pausa
        self.EstadoContador = 0
        self.EstadoPausa = False
        self.Flop = False
        
        # Mecanismo de proteção para deteção de falsos positivos
        self.last_count_time = 0
        self.count_threshold_ms = 50  # Intervalo mínimo entre contagens (ms)
        
        self.ContadorConfigurado = 0
        self.Quebras = 0
        
        # Proteção de leitura dos sensores
        self.read_error_count = 0
        self.max_read_errors = 10  # Máximo de erros antes de reiniciar leitura
        
        # Estatísticas com backup periódico
        self.TempoInicio = ""
        self.TempoFim = ""
        self.EstatisticaGFA = []
        self.EstatisticaGFAMedia = []
        self.EstatisticaGFANominal = 0
        self.EstatisticaTempo = []
        self.EstatisticaCadenciaArtigo = []
        self.RegistoParagem = 0
        self.GravarDados = 0
        self.Paragens = []
        
        self.ArtigoEmContagem = "NA"
        self.DescricaoArtigoEmContagem = "NA"
        self.CadenciaArtigoEmContagem = 6000
        
        self.ContagemAtual = 0
        self.ContagemTotal = 0
        self.EstadoPorta = 0
        self.Ordem = "NA"
        self.input_state = 0
        self.IdBDOrdemProducao = 0
        
        # Backup de estado para recuperação
        self.last_saved_state = {}
        self._save_state()
    
    def inicializar_sensor(self):
        """Inicializa o sensor de contagem com tratamento de erros"""
        try:
            # Limpar qualquer configuração anterior
            self._safe_gpio_cleanup()
            
            # Configurar GPIO para o sensor
            GPIO.setup(self.SENSOR_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP if self.pullup else GPIO.PUD_DOWN)
            
            # Pausa para estabilização antes de ler o estado
            time.sleep(0.2)
            
            # Ler o estado inicial do sensor e garantir que Flop começa como False
            self.previous_sensor_state = GPIO.input(self.SENSOR_PIN)
            self.Flop = False
            
            logging.info(f"Sensor inicializado no pino {self.SENSOR_PIN} com pull-up={self.pullup}, estado inicial={self.previous_sensor_state}")
            self.sensor_initialized = True
            return True
        except Exception as e:
            logging.error(f"Erro ao inicializar sensor: {str(e)}")
            self.sensor_initialized = False
            return False
    
    def inicializar_porta(self):
        """Inicializa o controle da porta com tratamento de erros"""
        try:
            # Configurar GPIO para a porta
            GPIO.setup(self.DOOR_PIN, GPIO.OUT)
            GPIO.output(self.DOOR_PIN, GPIO.LOW)  # Garantir que começa fechado
            self.EstadoPorta = 0
            
            logging.info(f"Porta inicializada no pino {self.DOOR_PIN}")
            self.door_initialized = True
            return True
        except Exception as e:
            logging.error(f"Erro ao inicializar porta: {str(e)}")
            self.door_initialized = False
            return False
    
    def reiniciar_sensor(self):
        """Reinicia o sensor com controle de frequência para evitar loops infinitos"""
        current_time = time.time()
        
        # Verificar se não estamos tentando reiniciar com muita frequência
        if current_time - self.sensor_last_reset < self.reset_cooldown:
            self.sensor_reset_attempts += 1
            if self.sensor_reset_attempts > self.max_reset_attempts:
                logging.error("Muitas tentativas de reiniciar o sensor - possível problema de hardware")
                return False
        else:
            # Reiniciar contador de tentativas
            self.sensor_reset_attempts = 0
        
        self.sensor_last_reset = current_time
        
        try:
            logging.warning("Reiniciando sensor de contagem")
            
            # Limpar configuração atual
            self._safe_gpio_cleanup()
            
            # Pequena pausa para estabilização
            time.sleep(0.2)
            
            # Reinicializar o sensor
            success = self.inicializar_sensor()
            
            if success:
                logging.info("Sensor reiniciado com sucesso")
                return True
            else:
                logging.error("Falha ao reiniciar sensor")
                return False
        except Exception as e:
            logging.error(f"Erro ao reiniciar sensor: {str(e)}")
            return False
    
    def _safe_gpio_cleanup(self):
        """Método seguro para limpar os pinos GPIO"""
        try:
            # Limpar apenas os pinos que estamos usando
            GPIO.cleanup([self.SENSOR_PIN, self.DOOR_PIN])
            logging.info("Limpeza de GPIO executada")
        except Exception as e:
            logging.warning(f"Erro durante limpeza de GPIO: {str(e)}")
    
    def _save_state(self):
        """Guarda o estado atual para recuperação em caso de falha"""
        with self._state_lock:
            self.last_saved_state = {
                'EstadoContador': self.EstadoContador,
                'ContagemAtual': self.ContagemAtual,
                'ContagemTotal': self.ContagemTotal,
                'Quebras': self.Quebras,
                'Ordem': self.Ordem,
                'IdBDOrdemProducao': self.IdBDOrdemProducao,
                'ArtigoEmContagem': self.ArtigoEmContagem,
                'TempoInicio': self.TempoInicio,
                'TempoFim': self.TempoFim,
                "EstadoPorta": self.EstadoPorta
            }
            
            # Guardar em ficheiro para persistência
            try:
                with open('contador_state.backup', 'w') as f:
                    for key, value in self.last_saved_state.items():
                        f.write(f"{key}={value}\n")
            except Exception as e:
                logging.error(f"Erro ao guardar estado em ficheiro: {str(e)}")
    
    def recover_state(self):
        """Recupera estado guardado em caso de reinicialização inesperada"""
        try:
            if os.path.exists('contador_state.backup'):
                with open('contador_state.backup', 'r') as f:
                    state = {}
                    for line in f:
                        if '=' in line:
                            key, value = line.strip().split('=', 1)
                            state[key] = value
                            
                with self._state_lock:
                    # Recuperar apenas se houver ordem ativa
                    if state.get('Ordem', 'NA') != 'NA':
                        self.EstadoContador = int(state.get('EstadoContador', 0))
                        self.ContagemAtual = int(state.get('ContagemAtual', 0))
                        self.ContagemTotal = int(state.get('ContagemTotal', 0))
                        self.Quebras = int(state.get('Quebras', 0))
                        self.Ordem = state.get('Ordem', 'NA')
                        self.IdBDOrdemProducao = int(state.get('IdBDOrdemProducao', 0))
                        self.ArtigoEmContagem = state.get('ArtigoEmContagem', 'NA')
                        self.TempoInicio = state.get('TempoInicio', '')
                        self.TempoFim = state.get('TempoFim', '')
                        self.EstadoPorta = int(state.get('EstadoPorta', 0))
                        
                        # Sincronizar o estado real da porta
                        if self.EstadoPorta == 1:
                            GPIO.output(self.DOOR_PIN, GPIO.HIGH)
                        else:
                            GPIO.output(self.DOOR_PIN, GPIO.LOW)
                        
                        # Se estava em contagem, pausar por segurança
                        if self.EstadoContador == 1:
                            self.EstadoContador = 2  # Pausa
                            logging.info("Recuperado de estado anterior - pausado por segurança")
                            # Definir como configurado
                            self.ContadorConfigurado = 1
                        
                        logging.info(f"Estado recuperado: Ordem={self.Ordem}, Contagem={self.ContagemAtual}/{self.ContagemTotal}")
        except Exception as e:
            logging.error(f"Erro ao recuperar estado: {str(e)}")
    
    def increment_count(self):
        """Incrementa a contagem com proteção contra falsas leituras"""
        current_time = time.time() * 1000  # Tempo atual em ms
        
        with self._contagem_lock:
            # Verifica se o tempo desde a última contagem é maior que o limiar
            if current_time - self.last_count_time > self.count_threshold_ms:
                self.ContagemAtual += 1
                self.last_count_time = current_time
                
                # Log para diagnóstico
                if self.ContagemAtual % 10 == 0:
                    logging.info(f"Contagem atual: {self.ContagemAtual}")
                
                # Verificar se atingiu o total
                if self.ContagemAtual >= (self.ContagemTotal + self.Quebras):
                    # Usar threading para não bloquear a contagem
                    threading.Thread(target=self._stop_counting_thread).start()
                
                # Guardar estado a cada 10 contagens
                if self.ContagemAtual % 10 == 0:
                    self._save_state()
                
                return True
            else:
                # Regista falsas leituras para diagnóstico
                logging.debug(f"Leitura ignorada: intervalo muito curto ({int(current_time - self.last_count_time)} ms)")
                return False
    
    def _stop_counting_thread(self):
        """Thread segura para parar contagem"""
        try:
            with self._state_lock:
                self.EstadoContador = 0
                self.TempoFim = datetime.now().replace(microsecond=0).strftime("%Y-%m-%d %H:%M:%S")
                self.GravarDados = 1
                self._save_state()
            
            # Fechar porta
            GPIO.output(self.DOOR_PIN, GPIO.LOW)
            self.EstadoPorta = 0
            
            logging.info("Contagem finalizada automaticamente")
        except Exception as e:
            logging.error(f"Erro ao parar contagem: {str(e)}")

    def update_stats(self):
        """Atualiza as estatísticas do contador"""
        try:
            # Só atualiza estatísticas se contador ativo
            if self.EstadoContador == 1:
                with self._contagem_lock:
                    contagem_inicial = self.ContagemAtual
                
                # Aguardar período para cálculo
                time.sleep(5)
                
                # Calcular estatísticas com proteção de thread
                with self._contagem_lock:
                    contagem_final = self.ContagemAtual
                    diff = contagem_final - contagem_inicial
                    
                # Calcular valor GFA (garrafas por hora)
                gfa = float(diff * 720)  # (diff / 5s) * 3600s = diff * 720
                
                with self._state_lock:
                    self.EstatisticaGFA.append(gfa)
                    self.EstatisticaGFANominal = gfa
                    
                    # Calcular média com proteção
                    try:
                        valid_values = [float(x) for x in self.EstatisticaGFA if isinstance(x, (int, float, np.float64, np.int64)) and x >= 0]
                        if valid_values:
                            media = float(round(np.mean(valid_values), 0))
                            self.EstatisticaGFAMedia.append(media)
                        else:
                            self.EstatisticaGFAMedia.append(0.0)
                    except Exception as media_e:
                        logging.error(f"Erro ao calcular média: {media_e}")
                        self.EstatisticaGFAMedia.append(0.0)
                    
                    # Registar tempo
                    self.EstatisticaTempo.append(datetime.now().strftime("%H:%M:%S"))
                    
                    # Atualizar cadência do artigo se disponível
                    if hasattr(self, 'CadenciaArtigoEmContagem'):
                        cadencia_valor = float(self.CadenciaArtigoEmContagem) if hasattr(self.CadenciaArtigoEmContagem, "__float__") else self.CadenciaArtigoEmContagem
                        self.EstatisticaCadenciaArtigo.append(cadencia_valor)
                    
                    # Registar ocorrência de paragens
                    if self.RegistoParagem == 1:
                        self.Paragens.append("0")
                        self.RegistoParagem = 0
                    else:
                        self.Paragens.append("null")
                    
                    # Limitar tamanho das listas para evitar uso excessivo de memória
                    max_list_size = 1000
                    if len(self.EstatisticaGFA) > max_list_size:
                        self.EstatisticaGFA = self.EstatisticaGFA[-max_list_size:]
                    if len(self.EstatisticaGFAMedia) > max_list_size:
                        self.EstatisticaGFAMedia = self.EstatisticaGFAMedia[-max_list_size:]
                    if len(self.EstatisticaTempo) > max_list_size:
                        self.EstatisticaTempo = self.EstatisticaTempo[-max_list_size:]
                    if len(self.EstatisticaCadenciaArtigo) > max_list_size:
                        self.EstatisticaCadenciaArtigo = self.EstatisticaCadenciaArtigo[-max_list_size:]
                    if len(self.Paragens) > max_list_size:
                        self.Paragens = self.Paragens[-max_list_size:]
                
                # Gravar na BD se necessário
                if self.IdBDOrdemProducao > 0:
                    threading.Thread(
                        target=gravar_contagem,
                        args=(self.IdBDOrdemProducao, contagem_final)
                    ).start()
            
            # Verificar se precisa finalizar registo na BD
            elif self.EstadoContador == 0 and self.GravarDados == 1:
                self.finalizar_registo_bd()
                
        except Exception as e:
            logging.error(f"Erro ao atualizar estatísticas: {e}")
            logging.error(traceback.format_exc())
            
            # Fazer backup do estado em caso de erro
            with self._state_lock:
                self._save_state()
    
    def finalizar_registo_bd(self):
        """Finaliza o registo na base de dados quando a contagem termina"""
        try:
            self.GravarDados = 0
            
            conn = get_db_connection(self.DB_Server, self.DB_User, self.DB_Password, self.DB_DB)
            cursor = conn.cursor()
            
            # Garantir que os valores são tipos básicos do Python antes de enviar à base de dados
            media_valor = float(media_producao()) if hasattr(media_producao(), "__float__") else 0
            
            cursor.execute(
                """
                UPDATE krones_contadoreslinha
                SET
                    Ativo = 0,
                    QuantidadeFinal = %s,
                    Quebras = %s,
                    MediaProducao = %s,
                    Abertura = %s,
                    Fecho = %s
                WHERE
                    Ativo = 1 AND
                    Ordem = %s AND
                    Id = %s
                """,
                (
                    int(self.ContagemAtual),
                    int(self.Quebras),
                    int(media_valor),
                    self.TempoInicio,
                    self.TempoFim,
                    self.Ordem,
                    int(self.IdBDOrdemProducao),
                )
            )
            
            conn.commit()
            conn.close()
            
            # Retirar configuração
            with self._state_lock:
                self.ContadorConfigurado = 0
                self._save_state()
            
            logging.info(f"Finalizada ordem {self.Ordem} na BD")
            
        except Exception as e:
            logging.error(f"Erro ao finalizar ordem na BD: {e}")
    
    def pause_count(self):
        """Pausa a contagem com proteção de estado"""
        with self._state_lock:
            if self.EstadoContador == 1: 
                self.EstadoContador = 2  # Pausa
                self.EstadoPausa = True
                logging.info("Contagem pausada com sucesso")
                self._save_state()  # Salvar o estado atual
                
                # Fechar a porta quando pausar
                try:
                    GPIO.output(self.DOOR_PIN, GPIO.LOW)
                    self.EstadoPorta = 0
                    logging.info("Porta fechada durante pausa")
                except Exception as e:
                    logging.error(f"Erro ao fechar porta durante pausa: {e}")
                    # Tentar recuperar
                    try:
                        GPIO.setup(self.DOOR_PIN, GPIO.OUT)
                        GPIO.output(self.DOOR_PIN, GPIO.LOW)
                        self.EstadoPorta = 0
                        logging.info("Recuperação de porta durante pausa bem-sucedida")
                    except Exception as e2:
                        logging.error(f"Falha na recuperação de porta durante pausa: {e2}")
                
                return True
            else:
                logging.info("Não é possível pausar: contador não está em modo de contagem")
                return False

    def resume_count(self):
        """Retoma a contagem que foi pausada"""
        with self._state_lock:
            if self.EstadoContador == 2:  # Só retoma se estiver pausado
                self.EstadoContador = 1  # Contagem
                self.EstadoPausa = False
                logging.info("Contagem retomada com sucesso")
                self._save_state()  # Salvar o estado atual
                
                # Abrir a porta
                try:
                    GPIO.output(self.DOOR_PIN, GPIO.HIGH)
                    self.EstadoPorta = 1
                except Exception as e:
                    logging.error(f"Erro ao abrir porta durante retomada: {e}")
                    # Tentar recuperar
                    try:
                        GPIO.setup(self.DOOR_PIN, GPIO.OUT)
                        GPIO.output(self.DOOR_PIN, GPIO.HIGH)
                        self.EstadoPorta = 1
                    except Exception as e2:
                        logging.error(f"Falha na recuperação de porta: {e2}")
                
                return True
            else:
                logging.info("Não é possível retomar: contador não está pausado")
                return False

# Instância do contador
contador = Contador()

# Variável global para controle de threads
thread_running = True  # Flag para controle de threads

# Tratamento de sinais para encerramento gracioso
def signal_handler(sig, frame):
    """Manipulador de sinais para encerramento seguro"""
    global contador, thread_running
    
    logging.info(f"Sinal {sig} recebido, preparando para desligar...")
    
    # Parar threads
    thread_running = False
    time.sleep(1)  # Dar tempo para as threads terminarem
    
    # Garantir que portas estão em estado seguro
    try:
        GPIO.output(contador.DOOR_PIN, GPIO.LOW)
        GPIO.output(contador.SENSOR_PIN, GPIO.LOW)
    except Exception as e:
        logging.error(f"Erro ao definir pinos como LOW: {e}")
    
    # Salvar estado atual
    try:
        contador._save_state()
        logging.info("Estado salvo com sucesso")
    except Exception as e:
        logging.error(f"Erro ao salvar estado: {e}")
    
    # Limpar GPIO
    try:
        GPIO.cleanup()
        logging.info("GPIO limpo com sucesso")
    except Exception as e:
        logging.error(f"Erro ao limpar GPIO: {e}")
    
    # Encerrar programa
    logging.info("Programa encerrado de forma limpa")
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

# Funções de controle da porta com proteção de exceções
@log_exceptions
def open_door():
    """Abre a porta com proteção contra falhas"""
    try:
        if contador.EstadoPorta == 0:
            GPIO.output(contador.DOOR_PIN, GPIO.HIGH)
            contador.EstadoPorta = 1
            logging.info("Porta aberta")
            return True
        return False
    except Exception as e:
        logging.error(f"Erro ao abrir porta: {str(e)}")
        # Tentativa de recuperação
        try:
            GPIO.setup(contador.DOOR_PIN, GPIO.OUT)
            GPIO.output(contador.DOOR_PIN, GPIO.HIGH)
            contador.EstadoPorta = 1
            logging.info("Recuperação de porta bem-sucedida")
            return True
        except Exception as e2:
            logging.error(f"Falha na recuperação de porta: {str(e2)}")
            return False

@log_exceptions
def close_door():
    """Fecha a porta com proteção contra falhas"""
    try:
        if contador.EstadoPorta == 1:
            GPIO.output(contador.DOOR_PIN, GPIO.LOW)
            contador.EstadoPorta = 0
            logging.info("Porta fechada")
            return True
        return False
    except Exception as e:
        logging.error(f"Erro ao fechar porta: {str(e)}")
        # Tentativa de recuperação
        try:
            GPIO.setup(contador.DOOR_PIN, GPIO.OUT)
            GPIO.output(contador.DOOR_PIN, GPIO.LOW)
            contador.EstadoPorta = 0
            logging.info("Recuperação de porta bem-sucedida")
            return True
        except Exception as e2:
            logging.error(f"Falha na recuperação de porta: {str(e2)}")
            return False

@log_exceptions
def reset_stats():
    """Reset aos dados estatísticos com proteção de thread"""
    with contador._state_lock:
        contador.TempoInicio = ""
        contador.TempoFim = ""
        contador.EstatisticaGFANominal = 0
        contador.EstatisticaGFA = []
        contador.EstatisticaGFAMedia = []
        contador.EstatisticaTempo = []
        contador.EstatisticaCadenciaArtigo = []
        contador.RegistoParagem = 0
        contador.Paragens = []
    
    logging.info("Estatísticas repostas")

@log_exceptions
def reset_counter():
    """Reset completo do contador com proteção de thread"""
    with contador._state_lock:
        contador.ArtigoEmContagem = "NA"
        contador.DescricaoArtigoEmContagem = "NA"
        contador.CadenciaArtigoEmContagem = 6000
        contador.ContagemAtual = 0
        contador.ContagemTotal = 0
        contador.Quebras = 0
        contador.EstadoContador = 0
        contador.EstadoPorta = 0
        contador.Ordem = "NA"
        contador.input_state = 0
        contador.IdBDOrdemProducao = 0
        contador.ContadorConfigurado = 0
        contador.Flop = False
        
        reset_stats()
        contador._save_state()
    
    # Garantir porta fechada
    close_door()
    logging.info("Contador completamente reposto")

# Endpoints da API com proteção de exceções
@app.route("/abrir-porta", methods=["GET"])
@log_exceptions
def abrir_porta():
    open_door()
    return jsonify({"status": "OK"}), 200

@app.route("/fechar-porta", methods=["GET"])
@log_exceptions
def fechar_porta():
    close_door()
    return jsonify({"status": "OK"}), 200

@app.route("/iniciar-contagem", methods=["GET"])
@log_exceptions
def iniciar_contagem():
    with contador._state_lock:
        reset_stats()
        contador.TempoInicio = datetime.now().replace(microsecond=0).strftime("%Y-%m-%d %H:%M:%S")
        contador.EstadoContador = 1
        contador._save_state()
    
    open_door()
    logging.info("Contagem iniciada")
    return jsonify({"status": "OK"}), 200

@app.route("/parar-contagem", methods=["GET"])
@log_exceptions
def parar_contagem():
    close_door()
    
    with contador._state_lock:
        contador.TempoFim = datetime.now().replace(microsecond=0).strftime("%Y-%m-%d %H:%M:%S")
        contador.EstadoContador = 0
        contador.ContadorConfigurado = 0
        contador.GravarDados = 1
        contador._save_state()
    
    logging.info("Contagem parada")
    return jsonify({"status": "OK"}), 200

@app.route('/pausa', methods=['GET'])
def pausar_contagem():
    """
    Pausa a contagem atual
    """
    logging.info("Solicitação para pausar contagem recebida")
    try:
        with contador._state_lock:
            if contador.pause_count():
                # O método pause_count já se encarrega de fechar a porta
                return jsonify({"status": "success", "message": "Contagem pausada com sucesso e porta fechada"}), 200
            else:
                return jsonify({"status": "error", "message": "Não foi possível pausar a contagem. Verifique se a contagem está em andamento."}), 400
    except Exception as e:
        logging.error(f"Erro ao pausar contagem: {str(e)}")
        return jsonify({"status": "error", "message": f"Erro ao pausar contagem: {str(e)}"}), 500

@app.route("/retomar", methods=["GET"])
def retomar_contagem():
    """
    Retoma a contagem que foi pausada
    """
    logging.info("Solicitação para retomar contagem recebida")
    try:
        with contador._state_lock:
            if contador.resume_count():
                return jsonify({"status": "success", "message": "Contagem retomada com sucesso"}), 200
            else:
                return jsonify({"status": "error", "message": "Não foi possível retomar a contagem. Verifique se a contagem está pausada."}), 400
    except Exception as e:
        logging.error(f"Erro ao retomar contagem: {str(e)}")
        return jsonify({"status": "error", "message": f"Erro ao retomar contagem: {str(e)}"}), 500

@app.route("/quebra/<int:valor>", methods=["GET"])
@log_exceptions
def quebra(valor):
    try:
        if contador.EstadoContador == 1:
            with contador._contagem_lock:
                contador.Quebras += valor
                contador._save_state()
            
            logging.info(f"Registada quebra de {valor} garrafas")
            return jsonify({"status": "OK"}), 200
        else:
            return jsonify({"status": "Erro", "mensagem": "Contador não está em contagem"}), 400
    except Exception as e:
        logging.error(f"Erro ao registrar quebra: {str(e)}")
        return jsonify({"status": "Erro", "mensagem": str(e)}), 500
# Confirmar se na base de dados está OK para gravar. BETA
@log_exceptions
def validate_active_orders():
    """Confirma se na base de dados está OK para gravar"""
    try:
        conn = get_db_connection(contador.DB_Server, contador.DB_User, contador.DB_Password, contador.DB_DB)
        cursor = conn.cursor()
        
        cursor.execute(
            """
            IF EXISTS (SELECT Id FROM krones_contadoreslinha WHERE Ativo = 1)
                SELECT COUNT(Id) AS Id FROM krones_contadoreslinha WHERE Ativo = 1
            ELSE
                SELECT '-1' AS Id
            """
        )
        
        row = cursor.fetchone()
        conn.close()
        
        return 0 if row[0] == "-1" else 1
    except Exception as e:
        logging.error(f"Erro ao validar ordens ativas: {e}")
        return -1

@log_exceptions
def media_producao():
    """Calcula média de produção com proteção contra lista vazia"""
    try:
        with contador._state_lock:
            if not contador.EstatisticaGFA or len(contador.EstatisticaGFA) == 0:
                return 0
                
            # Filtrar valores nulos ou inválidos
            valid_values = [float(x) for x in contador.EstatisticaGFA if isinstance(x, (int, float, np.float64, np.int64)) and x >= 0]
            
            if not valid_values:
                return 0
                
            # Se houver poucos valores, usar todos; caso contrário, usar os últimos 10
            if len(valid_values) <= 10:
                return float(round(np.mean(valid_values), 0))
            else:
                return float(round(np.mean(valid_values[-10:]), 0))
    except Exception as e:
        logging.error(f"Erro ao calcular média de produção: {e}")
        return 0

@app.route("/setup/<string:ordem>/<int:cnt>", methods=["GET"])
@log_exceptions
def setup_contagem(ordem, cnt):
    """Configura uma nova contagem com validação robusta e proteção contra falhas"""
    try:
        # Verificações iniciais
        if contador.ContadorConfigurado == 1:
            logging.info("Contador já configurado")
            return jsonify({"message": "Contador já configurado"}), 400

        if contador.EstadoContador != 0:
            logging.info("O contador não está parado!")
            return jsonify({"message": "Contador não está parado"}), 400

        # Validar ordens ativas
        active_orders = validate_active_orders()
        if active_orders == 1:
            logging.info("O contador está a registar, por favor aguarde.")
            return jsonify({"message": "O contador está a registar, por favor aguarde."}), 400
        elif active_orders == -1:
            logging.error("Erro ao validar ordens ativas")
            return jsonify({"message": "Erro ao validar ordens ativas"}), 500

        # Se passou nas verificações, pode configurar
        if contador.EstadoContador == 0 and contador.ContadorConfigurado == 0:
            # Reset às estatísticas
            reset_stats()
            
            with contador._state_lock:
                contador.ContadorConfigurado = 1
                contador.ContagemTotal = cnt
                contador.Ordem = ordem
                contador.ContagemAtual = 0
                contador.Quebras = 0
            
            # Obter informações do artigo na primeira BD
            try:
                conn = get_db_connection("[DB_SERVER]", "[DB_USER]", "[DB_PASSWORD]", "[DB_DATABASE]")
                cursor = conn.cursor()
                
                # Limpar o número da ordem para a consulta
                ordem_cleaned = ordem.replace('-', '/')
                
                cursor.execute(
                    """
                    SELECT
                        ArtigoGCP, DescricaoGCP, ISNULL(CDU_Cadencia, 6000) AS CDU_Cadencia
                    FROM
                        [DB_DATABASE].dbo.prd_ORDEM_PRODUCAO
                    INNER JOIN
                        [DB_DATABASE].dbo.Artigo
                    ON
                        prd_ORDEM_PRODUCAO.ArtigoGCP = Artigo.Artigo
                    WHERE 
                        nEMPRESA = 1 AND
                        NORDEM = %s
                    """, 
                    (ordem_cleaned,)
                )
                
                row = cursor.fetchone()
                conn.close()
                
                # Atualizar dados do artigo
                if row:
                    with contador._state_lock:
                        contador.ArtigoEmContagem = row[0]
                        contador.DescricaoArtigoEmContagem = str(row[1])
                        contador.CadenciaArtigoEmContagem = row[2]
                else:
                    logging.warning(f"Artigo não encontrado para ordem {ordem}")
                    with contador._state_lock:
                        contador.ArtigoEmContagem = "DESCONHECIDO"
                        contador.DescricaoArtigoEmContagem = "Artigo não encontrado"
                        contador.CadenciaArtigoEmContagem = 6000
            except Exception as e:
                logging.error(f"Erro ao obter dados do artigo: {e}")
                with contador._state_lock:
                    contador.ArtigoEmContagem = "ERRO"
                    contador.DescricaoArtigoEmContagem = "Erro ao obter dados"
                    contador.CadenciaArtigoEmContagem = 6000
            
            # Registar na BD SIP
            try:
                conn = get_db_connection(contador.DB_Server, contador.DB_User, contador.DB_Password, contador.DB_DB)
                cursor = conn.cursor()
                
                # Registar a ordem de produção
                cursor.execute(
                    """
                    INSERT INTO krones_contadoreslinha
                        (Data, Ativo, Ordem, QuantidadeInicial, Artigo)
                    VALUES
                        (%s, %s, %s, %s, %s)
                    """,
                    (
                        datetime.now().replace(microsecond=0).strftime("%Y-%m-%d %H:%M:%S"),
                        1,
                        ordem,
                        cnt,
                        contador.ArtigoEmContagem,
                    )
                )
                conn.commit()
                
                # Obter ID da ordem inserida
                cursor.execute(
                    """
                    SELECT
                        Id
                    FROM
                        krones_contadoreslinha
                    WHERE
                        Ativo = 1 AND
                        Ordem = %s
                    """,
                    (ordem,)
                )
                
                row = cursor.fetchone()
                conn.close()
                
                if row:
                    with contador._state_lock:
                        contador.IdBDOrdemProducao = row[0]
                        contador._save_state()
                
                logging.info(f"Ordem {ordem} configurada com {cnt} garrafas totais")
                return jsonify({"message": f"Ordem {ordem} configurada com {cnt} garrafas totais"}), 200
            except Exception as e:
                logging.error(f"Erro ao registar ordem na BD: {e}")
                return jsonify({"message": f"Erro ao registar ordem: {str(e)}"}), 500
    except Exception as e:
        logging.error(f"Erro ao configurar contagem: {e}")
        return jsonify({"message": f"Erro ao configurar contagem: {str(e)}"}), 500

@app.route("/reset-contador", methods=["GET"])
@log_exceptions
def reset_contador_endpoint():
    """Endpoint para repor o contador"""
    try:
        if contador.EstadoContador == 0:
            # Marcar todas as ordens como inativas
            try:
                conn = get_db_connection(contador.DB_Server, contador.DB_User, contador.DB_Password, contador.DB_DB)
                cursor = conn.cursor()
                
                cursor.execute(
                    """
                    UPDATE krones_contadoreslinha
                    SET Ativo = 0
                    WHERE Ativo = 1
                    """
                )
                
                conn.commit()
                conn.close()
            except Exception as e:
                logging.error(f"Erro ao atualizar BD durante reset: {e}")
                return jsonify({"message": f"Erro ao atualizar BD: {str(e)}"}), 500
            
            # Repor o contador
            reset_counter()
            return jsonify({"message": "Contador reposto com sucesso"}), 200
        else:
            return jsonify({"message": "Contador não está parado"}), 400
    except Exception as e:
        logging.error(f"Erro ao repor contador: {e}")
        return jsonify({"message": f"Erro ao repor contador: {str(e)}"}), 500

@app.route("/status", methods=["GET"])
@log_exceptions
def status():
    """Retorna o status atual do contador"""
    try:
        with contador._state_lock:
            # Obter a hora de início da ordem atual
            inicio_ordem = None
            if contador.TempoInicio:
                try:
                    inicio_ordem = datetime.strptime(contador.TempoInicio, "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    logging.warning(f"Formato de data de início inválido: {contador.TempoInicio}")
            
            # Filtrar arrays de estatísticas para incluir apenas dados dentro do período válido
            filtered_stats = {}
            if inicio_ordem:
                # Verificar quais índices dos arrays têm timestamps posteriores ao início da ordem
                valid_indices = []
                for i, tempo_str in enumerate(contador.EstatisticaTempo):
                    try:
                        # O formato no array é apenas hora:minuto:segundo
                        # Precisamos combiná-lo com a data de início
                        hora_parts = tempo_str.split(':')
                        if len(hora_parts) == 3:
                            hora, minuto, segundo = map(int, hora_parts)
                            tempo_stat = inicio_ordem.replace(hour=hora, minute=minuto, second=segundo)
                            
                            # Se a hora for menor que a do início da ordem e for no mesmo dia,
                            # provavelmente é do dia seguinte
                            if tempo_stat < inicio_ordem:
                                tempo_stat = tempo_stat + timedelta(days=1)
                                
                            if tempo_stat >= inicio_ordem:
                                valid_indices.append(i)
                    except Exception as e:
                        logging.error(f"Erro ao processar tempo de estatística {tempo_str}: {e}")
                
                # Filtrar os arrays usando os índices válidos
                filtered_stats["EstatisticaGFA"] = [contador.EstatisticaGFA[i] for i in valid_indices if i < len(contador.EstatisticaGFA)]
                filtered_stats["EstatisticaGFAMedia"] = [contador.EstatisticaGFAMedia[i] for i in valid_indices if i < len(contador.EstatisticaGFAMedia)]
                filtered_stats["EstatisticaTempo"] = [contador.EstatisticaTempo[i] for i in valid_indices if i < len(contador.EstatisticaTempo)]
                filtered_stats["EstatisticaCadenciaArtigo"] = [contador.EstatisticaCadenciaArtigo[i] for i in valid_indices if i < len(contador.EstatisticaCadenciaArtigo)]
                filtered_stats["Paragens"] = [contador.Paragens[i] for i in valid_indices if i < len(contador.Paragens)]
            else:
                # Se não tiver início, usar todos os dados (caso raro)
                filtered_stats["EstatisticaGFA"] = contador.EstatisticaGFA
                filtered_stats["EstatisticaGFAMedia"] = contador.EstatisticaGFAMedia
                filtered_stats["EstatisticaTempo"] = contador.EstatisticaTempo
                filtered_stats["EstatisticaCadenciaArtigo"] = contador.EstatisticaCadenciaArtigo
                filtered_stats["Paragens"] = contador.Paragens
            
            # Criar objeto de resposta
            data = {
                "Ordem": contador.Ordem,
                "Artigo": contador.ArtigoEmContagem,
                "DescricaoArtigo": contador.DescricaoArtigoEmContagem,
                "CadenciaArtigo": contador.CadenciaArtigoEmContagem,
                "Inicio": contador.TempoInicio,
                "Fim": contador.TempoFim,
                "ContagemAtual": contador.ContagemAtual,
                "ContagemTotal": contador.ContagemTotal,
                "MediaProducao": media_producao(),
                "Nominal": filtered_stats["EstatisticaGFA"],
                "Media": filtered_stats["EstatisticaGFAMedia"],
                "Tempo": filtered_stats["EstatisticaTempo"],
                "Cadencia": filtered_stats["EstatisticaCadenciaArtigo"],
                "Paragens": filtered_stats["Paragens"],
                "Quebras": contador.Quebras,
                "EstadoPorta": contador.EstadoPorta,
                "EstadoContador": contador.EstadoContador,
                "EstadoConfiguracao": contador.ContadorConfigurado,
                "IdBDOrdemProducao": contador.IdBDOrdemProducao,
                "DataDados": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
            
            # Calcular estimativa de conclusão, se aplicável
            if data["MediaProducao"] > 0 and contador.EstadoContador == 1:
                try:
                    minutos_restantes = math.ceil(
                        (data["ContagemTotal"] - data["ContagemAtual"]) * 60 / 
                        data["MediaProducao"]
                    )
                    data["EstimativaFecho"] = (
                        datetime.now() + timedelta(minutes=minutos_restantes)
                    ).strftime("%Y-%m-%d %H:%M:%S")
                except Exception as e:
                    logging.error(f"Erro ao calcular EstimativaFecho: {e}")
                    data["EstimativaFecho"] = ""
            else:
                data["EstimativaFecho"] = ""
            
        return jsonify({"data": data}), 200
    except Exception as e:
        logging.error(f"Erro ao obter status: {e}")
        return jsonify({"data": {}, "error": str(e)}), 500

@log_exceptions
def obter_dados_historico(NumPontos, Ordem):
    """Função para conectar à base de dados e obter os dados da tabela historico_contagens"""
    try:
        conn = get_db_connection(contador.DB_Server, contador.DB_User, contador.DB_Password, contador.DB_DB)
        cursor = conn.cursor(as_dict=True)
        
        SQL = """
            SELECT TOP (%s)
                DataDados, Ordem, Artigo, DescricaoArtigo, CadenciaArtigo, 
                Inicio, Fim, ContagemAtual, ContagemTotal, MediaProducao, 
                Paragens, Quebras, EstadoPorta, EstadoContador, EstadoConfiguracao, 
                Nominal, Media, Cadencia, Tempo
            FROM krones_historico_contagens
            WHERE Ordem = %s
            ORDER BY DataDados ASC
        """
        
        cursor.execute(SQL, (NumPontos, Ordem))
        result = cursor.fetchall()
        conn.close()
        
        return result
    except Exception as e:
        logging.error(f"Erro ao obter dados históricos: {e}")
        return []

@app.route("/api/info", defaults={"NumPontos": 180, "Ordem": None})
@app.route("/api/info/<int:NumPontos>/<string:Ordem>")
@log_exceptions
def ApiInfo(NumPontos, Ordem):
    """API para obter informações históricas de uma ordem específica"""
    try:
        # Se a ordem não for fornecida, usar a ordem atual
        if Ordem is None:
            Ordem = contador.Ordem
            
        # Se ainda for None, retornar dados vazios
        if Ordem == "NA" or Ordem is None:
            return jsonify({
                "error": "Nenhuma ordem ativa ou especificada"
            }), 200
        
        # Obtém os dados da tabela historico_contagens
        result = obter_dados_historico(NumPontos, Ordem)
        
        if not result:
            return jsonify({
                "error": f"Sem dados históricos para a ordem {Ordem}"
            }), 200
        
        # Obtém a data/hora de início oficial da ordem diretamente da BD
        inicio_oficial_str = obter_inicio_oficial_ordem(Ordem)
        inicio_oficial = None
        
        # Converter para objeto datetime para comparação
        if inicio_oficial_str:
            try:
                inicio_oficial = datetime.strptime(inicio_oficial_str, "%Y-%m-%d %H:%M:%S")
                logging.info(f"Hora de início oficial para ordem {Ordem}: {inicio_oficial_str}")
                
                # Extrair apenas a hora, minuto e segundo para comparação direta
                inicio_hms = inicio_oficial.strftime("%H:%M:%S")
                logging.info(f"Hora de início formatada para comparação: {inicio_hms}")
            except ValueError as e:
                logging.error(f"Erro ao converter hora de início: {e}")
        
        # Dicionário para armazenar os dados consolidados
        dados_consolidados = {
            "DataDados": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "Ordem": Ordem,
            "Artigo": None,
            "DescricaoArtigo": None,
            "CadenciaArtigo": None,
            "Inicio": inicio_oficial_str,  # Usar a data/hora oficial obtida da BD
            "Fim": None,
            "ContagemAtual": 0,
            "ContagemTotal": 0,
            "MediaProducao": 0.0,
            "EstimativaFecho": "",
            "Nominal": [],
            "Paragens": [],
            "Quebras": 0,
            "EstadoPorta": 0,
            "EstadoContador": 0,
            "EstadoConfiguracao": 0,
            "Media": [],
            "Cadencia": [],
            "Tempo": [],
        }
        
        # Função auxiliar de segurança para extrair valores com proteção contra None
        def obter_valor_seguro(obj, chave, valor_padrao=None):
            """Retorna um valor de um dicionário com proteção contra None"""
            if obj is None:
                return valor_padrao
            
            valor = obj.get(chave, valor_padrao)
            return valor
        
        # Consolidar dados usando a última linha do resultado
        ultima_linha = result[-1] if result else None
        
        if ultima_linha:
            dados_consolidados["Artigo"] = ultima_linha.get("Artigo")
            dados_consolidados["DescricaoArtigo"] = ultima_linha.get("DescricaoArtigo")
            dados_consolidados["CadenciaArtigo"] = int(ultima_linha.get("CadenciaArtigo")) if ultima_linha.get("CadenciaArtigo") else 6000
            
            if ultima_linha.get("Fim") is not None:
                if hasattr(ultima_linha["Fim"], "strftime"):
                    dados_consolidados["Fim"] = ultima_linha["Fim"].strftime("%Y-%m-%d %H:%M:%S")
                else:
                    dados_consolidados["Fim"] = str(ultima_linha["Fim"])
            
            dados_consolidados["ContagemAtual"] = ultima_linha.get("ContagemAtual", 0)
            dados_consolidados["ContagemTotal"] = ultima_linha.get("ContagemTotal", 0)
            dados_consolidados["MediaProducao"] = ultima_linha.get("MediaProducao", 0)
            dados_consolidados["Quebras"] = ultima_linha.get("Quebras", 0)
            dados_consolidados["EstadoPorta"] = ultima_linha.get("EstadoPorta", 0)
            dados_consolidados["EstadoContador"] = ultima_linha.get("EstadoContador", 0)
            dados_consolidados["EstadoConfiguracao"] = ultima_linha.get("EstadoConfiguracao", 0)
        
        # Arrays para armazenar os dados filtrados
        tempo_filtrado = []
        paragens_filtrado = []
        nominal_filtrado = []
        media_filtrado = []
        cadencia_filtrado = []
        
        # Processar todos os dados das séries temporais com filtragem
        for idx, row in enumerate(result):
            tempo_str = None
            
            # Extrair tempo desta linha
            if obter_valor_seguro(row, "Tempo") is not None:
                tempo_str = row["Tempo"]
            elif row.get("DataDados") and hasattr(row["DataDados"], "strftime"):
                tempo_str = row["DataDados"].strftime("%H:%M:%S")
            else:
                tempo_str = "00:00:00"
            
            # Verificar se este tempo é posterior ao início oficial por comparação direta de strings
            incluir_registo = False
            
            if inicio_oficial and tempo_str:
                try:
                    # Comparação direta de strings HH:MM:SS
                    # Isto funciona porque estamos a comparar formatos iguais
                    incluir_registo = tempo_str >= inicio_hms
                    
                    # Log detalhado para os primeiros registos para diagnóstico
                    if idx < 5 or (idx > 50 and idx < 62):
                        logging.info(f"Registo {idx}: Tempo={tempo_str}, Início={inicio_hms}, Incluir={incluir_registo}")
                except Exception as e:
                    logging.error(f"Erro ao comparar tempo {tempo_str}: {e}")
                    incluir_registo = False
            else:
                # Se não temos início oficial, incluir todos
                incluir_registo = True
            
            # Só adicionar se o tempo for válido (posterior ao início)
            if incluir_registo:
                tempo_filtrado.append(tempo_str)
                
                # Adicionar os outros dados correspondentes
                if obter_valor_seguro(row, "Paragens") is not None:
                    paragens_filtrado.append(row["Paragens"])
                else:
                    paragens_filtrado.append(None)
                    
                if obter_valor_seguro(row, "Nominal") is not None:
                    nominal_filtrado.append(float(row["Nominal"]))
                else:
                    nominal_filtrado.append(0)
                    
                if obter_valor_seguro(row, "Media") is not None:
                    media_filtrado.append(float(row["Media"]))
                else:
                    media_filtrado.append(0)
                    
                if obter_valor_seguro(row, "Cadencia") is not None:
                    cadencia_filtrado.append(float(row["Cadencia"]))
                else:
                    cadencia_filtrado.append(dados_consolidados["CadenciaArtigo"])
        
        # Atualizar os dados consolidados com os arrays filtrados
        dados_consolidados["Tempo"] = tempo_filtrado
        dados_consolidados["Paragens"] = paragens_filtrado
        dados_consolidados["Nominal"] = nominal_filtrado 
        dados_consolidados["Media"] = media_filtrado
        dados_consolidados["Cadencia"] = cadencia_filtrado
        
        # Log para diagnóstico
        logging.info(f"Total de registos: {len(result)}, Registos filtrados: {len(tempo_filtrado)}")
        if tempo_filtrado:
            logging.info(f"Primeiro tempo filtrado: {tempo_filtrado[0]}, Último tempo filtrado: {tempo_filtrado[-1]}")
        
        # Adicionar campos em falta
        dados_consolidados["IdBDOrdemProducao"] = contador.IdBDOrdemProducao if Ordem == contador.Ordem else None
        
        # Calcular EstimativaFecho
        if dados_consolidados["MediaProducao"] > 0 and (contador.EstadoContador == 1 and Ordem == contador.Ordem):
            minutos_restantes = math.ceil(
                (dados_consolidados["ContagemTotal"] - dados_consolidados["ContagemAtual"]) * 60 / 
                dados_consolidados["MediaProducao"]
            )
            dados_consolidados["EstimativaFecho"] = (
                datetime.now() + timedelta(minutes=minutos_restantes)
            ).strftime("%Y-%m-%d %H:%M:%S")
        
        # Retorna os dados consolidados em JSON
        return jsonify(dados_consolidados), 200
    
    except Exception as e:
        logging.error(f"Erro na API info: {str(e)}")
        return jsonify({"error": str(e)}), 500

@log_exceptions
def obter_inicio_oficial_ordem(Ordem):
    """
    Obtém a data/hora de início oficial da ordem diretamente da BD.
    
    Esta função:
    1. Primeiro procura na tabela krones_contadoreslinha pelo campo Abertura
    2. Se não encontrar, procura na tabela krones_historico_contagens pelo campo Inicio
    3. Retorna None se não encontrar nenhuma data/hora de início
    
    Args:
        Ordem: O código da ordem de produção
        
    Returns:
        String com a data/hora de início formatada ou None
    """
    try:
        conn = get_db_connection(contador.DB_Server, contador.DB_User, contador.DB_Password, contador.DB_DB)
        cursor = conn.cursor(as_dict=True)
        
        # Obter a data de início a partir da tabela krones_contadoreslinha
        SQL = """
            SELECT Abertura
            FROM krones_contadoreslinha
            WHERE Ordem = %s
            ORDER BY Id DESC
        """
        
        cursor.execute(SQL, (Ordem,))
        row = cursor.fetchone()
        conn.close()
        
        if row and row.get("Abertura"):
            # Formatar data corretamente
            if hasattr(row["Abertura"], "strftime"):
                return row["Abertura"].strftime("%Y-%m-%d %H:%M:%S")
            else:
                return str(row["Abertura"])
        
        # Se não encontrou, tenta buscar no histórico
        conn = get_db_connection(contador.DB_Server, contador.DB_User, contador.DB_Password, contador.DB_DB)
        cursor = conn.cursor(as_dict=True)
        
        SQL = """
            SELECT TOP 1 Inicio
            FROM krones_historico_contagens
            WHERE Ordem = %s
            ORDER BY DataDados ASC
        """
        
        cursor.execute(SQL, (Ordem,))
        row = cursor.fetchone()
        conn.close()
        
        if row and row.get("Inicio"):
            # Formatar data corretamente
            if hasattr(row["Inicio"], "strftime"):
                return row["Inicio"].strftime("%Y-%m-%d %H:%M:%S")
            else:
                return str(row["Inicio"])
        
        # Se ainda não encontrou, retorna None
        return None
    except Exception as e:
        logging.error(f"Erro ao obter início oficial da ordem {Ordem}: {e}")
        return None

@log_exceptions
def gravar_contagem(Id, ContagemAtual):
    """Grava a contagem atual na BD com proteção contra falhas"""
    try:
        conn = get_db_connection(contador.DB_Server, contador.DB_User, contador.DB_Password, contador.DB_DB)
        cursor = conn.cursor()
        
        media = media_producao()
        EstimativaTempo = None
        
        agora = datetime.now()
        DataDados = agora.strftime("%Y-%m-%d %H:%M:%S")
        
        # Calcular estimativa de tempo
        if media > 0 and contador.EstadoContador == 1:
            try:
                Minutos = math.ceil((contador.ContagemTotal - ContagemAtual) * 60 / media)
                EstimativaTempo = (agora + timedelta(minutes=Minutos)).strftime("%Y-%m-%d %H:%M:%S")
            except Exception as e:
                logging.error(f"Erro ao calcular estimativa de tempo: {e}")
                EstimativaTempo = None
        
        # Formatar as datas para inserção no SQL
        Inicio = contador.TempoInicio if contador.TempoInicio else None
        Fim = contador.TempoFim if contador.TempoFim else None
        
        try:
            # Registar na tabela de contagem
            cursor.execute(
                """
                INSERT INTO krones_contadoreslinhacontagem
                    (IdContagem, ContagemAtual, Objetivo, DataLeitura)
                VALUES
                    (%s, %s, %s, %s)
                """,
                (
                    int(Id),
                    int(ContagemAtual),
                    int(contador.ContagemTotal),
                    DataDados,
                )
            )
            conn.commit()
            
            # Registar na tabela de histórico
            params = {
                "DataDados": DataDados,
                "Ordem": contador.Ordem,
                "Artigo": contador.ArtigoEmContagem,
                "DescricaoArtigo": contador.DescricaoArtigoEmContagem,
                "CadenciaArtigo": int(contador.CadenciaArtigoEmContagem) if hasattr(contador.CadenciaArtigoEmContagem, "__int__") else contador.CadenciaArtigoEmContagem,
                "Inicio": Inicio,
                "Fim": Fim,
                "ContagemAtual": int(ContagemAtual),
                "ContagemTotal": int(contador.ContagemTotal),
                "MediaProducao": float(media) if hasattr(media, "__float__") else media,
                "EstimativaFecho": EstimativaTempo,
                "Paragens": contador.Paragens[-1] if contador.Paragens else None,
                "Quebras": int(contador.Quebras),
                "EstadoPorta": int(contador.EstadoPorta),
                "EstadoContador": int(contador.EstadoContador),
                "EstadoConfiguracao": int(contador.ContadorConfigurado),
                "Nominal": float(contador.EstatisticaGFA[-1]) if contador.EstatisticaGFA else None,
                "Media": float(contador.EstatisticaGFAMedia[-1]) if contador.EstatisticaGFAMedia else None,
                "Cadencia": float(contador.EstatisticaCadenciaArtigo[-1]) if contador.EstatisticaCadenciaArtigo else None,
                "Tempo": contador.EstatisticaTempo[-1] if contador.EstatisticaTempo else None,
            }
            
            # Construir a SQL de forma dinâmica para lidar com parâmetros nulos
            sql_fields = []
            sql_values = []
            for key, value in params.items():
                if value is not None:
                    sql_fields.append(key)
                    sql_values.append(f"%({key})s")
            
            SQL = f"""
                INSERT INTO krones_historico_contagens
                    ({', '.join(sql_fields)})
                VALUES
                    ({', '.join(sql_values)})
            """
            
            cursor.execute(SQL, params)
            conn.commit()
            
        except pymssql.Error as e:
            logging.error(f"Erro SQL ao gravar contagem: {e}")
            # Tentar reconectar e repetir
            try:
                conn = get_db_connection(contador.DB_Server, contador.DB_User, contador.DB_Password, contador.DB_DB)
                cursor = conn.cursor()
                # ... repetir código de gravação aqui se necessário
            except Exception as retry_e:
                logging.error(f"Falha na reconexão: {retry_e}")
        
        finally:
            if conn:
                conn.close()
                
    except Exception as e:
        logging.error(f"Erro ao gravar contagem: {e}")

@log_exceptions
def count_thread():
    """Thread para contar eventos do sensor usando o sistema Flop"""
    global contador, thread_running

    logging.info("Thread de contagem iniciada com sistema Flop")
    
    # Variáveis locais para controlo
    contador_debug = 0
    ultimo_relatorio_estado = 0
    
    # Loop principal da thread
    while thread_running:
        try:
            tempo_atual = time.time()
            contador_debug += 1
            
            # Imprimir o estado do sensor periodicamente para diagnóstico
            if (tempo_atual - ultimo_relatorio_estado) > 30:
                if contador.sensor_initialized:
                    estado_sensor = GPIO.input(contador.SENSOR_PIN)
                    logging.info(f"Estado atual do sensor: {estado_sensor} (Modo contagem: {contador.EstadoContador}, Pausa: {contador.EstadoPausa}, Flop: {contador.Flop})")
                else:
                    logging.warning("Sensor não está inicializado, impossível ler estado")
                ultimo_relatorio_estado = tempo_atual
            
            # Verificar se está em modo de contagem ativo
            if contador.EstadoContador == 1 and not contador.EstadoPausa:
                # Ler o estado atual do sensor
                if contador.sensor_initialized:
                    estado_entrada = GPIO.input(contador.SENSOR_PIN)
                    
                    # Sistema Flop - primeira parte (deteta quando o sensor é ativado)
                    if estado_entrada == 1 and contador.Flop is False:
                        # Levantar FLOP - sensor ativado
                        contador.Flop = True
                        if contador_debug % 10 == 0:
                            logging.info("Sensor ativado - Flop levantado")
                            
                    # Sistema Flop - segunda parte (deteta quando o sensor volta ao estado normal)
                    if estado_entrada == 0 and contador.Flop is True:
                        # Contagem completa - incrementar contador
                        with contador._contagem_lock:
                            contador.ContagemAtual += 1
                            
                            # Log para diagnóstico
                            if contador.ContagemAtual % 10 == 0 or contador_debug % 20 == 0:
                                logging.info(f"Contagem incrementada: {contador.ContagemAtual}")
                            
                            # Verificar se atingiu o total
                            if contador.ContagemAtual >= (contador.ContagemTotal + contador.Quebras):
                                # Usar threading para não bloquear a contagem
                                threading.Thread(target=contador._stop_counting_thread).start()
                            
                            # Guardar estado a cada 10 contagens
                            if contador.ContagemAtual % 10 == 0:
                                contador._save_state()
                        
                        # Reset no FLOP - pronto para próxima contagem
                        contador.Flop = False
                        if contador_debug % 10 == 0:
                            logging.info("Sensor desativado - Flop reposto")
                else:
                    # Tentar reinicializar o sensor se não estiver inicializado
                    if contador.reiniciar_sensor():
                        logging.info("Sensor reinicializado com sucesso durante a thread de contagem")
                    else:
                        logging.error("Falha ao reinicializar sensor durante a thread de contagem")
                        time.sleep(5)  # Esperar antes de tentar novamente
            
            # Pequena pausa para não sobrecarregar o CPU
            time.sleep(0.01)  # 10ms, intervalo ótimo para deteção precisa
                
        except Exception as e:
            logging.error(f"Erro na thread de contagem: {e}")
            contador.read_error_count += 1
            
            # Se muitos erros consecutivos, tenta reiniciar o sensor
            if contador.read_error_count > contador.max_read_errors:
                logging.warning(f"Muitos erros consecutivos ({contador.read_error_count}), tentando reiniciar sensor")
                contador.reiniciar_sensor()
                contador.read_error_count = 0
                
            time.sleep(1)  # Pausa para evitar ciclos de erro em alta frequência
    
    logging.info("Thread de contagem terminada normalmente")

@log_exceptions
def stats_thread():
    """Thread dedicada à atualização periódica das estatísticas"""
    global thread_running
    logging.info("Thread de estatísticas iniciada")
    
    try:
        while thread_running:
            try:
                # Atualizar estatísticas usando o método da classe contador
                contador.update_stats()
                
            except Exception as e:
                logging.error(f"Erro na thread de estatísticas: {e}")
                logging.error(traceback.format_exc())
            
            # Aguardar próximo ciclo (5 segundos)
            time.sleep(5)
    
    except Exception as outer_e:
        logging.error(f"Erro fatal na thread de estatísticas: {outer_e}")
        logging.error(traceback.format_exc())
    
    logging.info("Thread de estatísticas finalizada")

@log_exceptions
def auto_pause_thread():
    """Thread para pausar automaticamente por inatividade (10 minutos)"""
    global thread_running
    logging.info("Thread de pausa automática iniciada")
    
    try:
        ultima_contagem = contador.ContagemAtual
        tempo_ultima_atividade = time.time()
        
        while thread_running:
            try:
                contagem_atual = contador.ContagemAtual
                
                # Se o contador estiver ativo e não houver mudança na contagem
                if contador.EstadoContador == 1:
                    if contagem_atual == ultima_contagem:
                        # Verificar tempo desde a última atividade
                        if time.time() - tempo_ultima_atividade > 600:  # 10 minutos de inatividade
                            if contador.pause_count():  # pause_count já fecha a porta
                                logging.info("Contador pausado automaticamente após 10 minutos de inatividade")
                    else:
                        # Houve contagem, atualizar momento da última atividade
                        tempo_ultima_atividade = time.time()
                        ultima_contagem = contagem_atual
                
            except Exception as e:
                logging.error(f"Erro na verificação de inatividade: {e}")
            
            # Verificar a cada 30 segundos
            time.sleep(30)
    
    except Exception as outer_e:
        logging.error(f"Erro fatal na thread de pausa automática: {outer_e}")
        logging.error(traceback.format_exc())
    
    logging.info("Thread de pausa automática finalizada")

@log_exceptions
def init_main():
    """Inicialização do sistema principal com recuperação de estado e threads"""
    global contador, thread_running
    thread_running = True
    
    try:
        # Limpar qualquer configuração GPIO anterior
        try:
            GPIO.cleanup()
            logging.info("Limpeza inicial de pinos GPIO concluída")
        except Exception as e:
            logging.warning(f"Falha na limpeza inicial de pinos: {str(e)}")
        
        # Configurar modo GPIO
        GPIO.setmode(GPIO.BCM)
        logging.info("Modo GPIO configurado como BCM")
        
        # Verificar se há um estado anterior para recuperar
        try:
            contador.recover_state()
            logging.info("Estado anterior recuperado")
        except Exception as e:
            logging.error(f"Erro ao recuperar estado anterior: {str(e)}")
        
        # Inicializar sensor com tratamento de erros
        if contador.inicializar_sensor():
            logging.info("Sensor inicializado com sucesso")
        else:
            logging.error("Falha na inicialização do sensor - continuando com sensor desativado")
        
        # Inicializar porta com tratamento de erros
        if contador.inicializar_porta():
            logging.info("Porta inicializada com sucesso")
            
            # Definir estado inicial da porta
            if contador.EstadoPorta == 1:
                GPIO.output(contador.DOOR_PIN, GPIO.HIGH)
                logging.info("Porta definida como ABERTA no início")
            else:
                GPIO.output(contador.DOOR_PIN, GPIO.LOW)
                logging.info("Porta definida como FECHADA no início")
        else:
            logging.error("Falha na inicialização da porta")
        
        # Iniciar threads com tratamento de exceções
        threads = [
            threading.Thread(target=count_thread, daemon=True, name="ContadorThread"),
            threading.Thread(target=stats_thread, daemon=True, name="EstatísticasThread"),
            threading.Thread(target=auto_pause_thread, daemon=True, name="PausaAutomáticaThread")
        ]
        
        for t in threads:
            try:
                t.start()
                logging.info(f"Thread {t.name} iniciada")
            except Exception as thread_e:
                logging.error(f"Erro ao iniciar thread {t.name}: {thread_e}")
        
        logging.info("Sistema inicializado com sucesso usando modo polling")
    except Exception as e:
        logging.critical(f"Erro fatal na inicialização: {e}")
        logging.critical(traceback.format_exc())
        raise

@app.route("/configurar-sensor", methods=["GET"])
@log_exceptions
def configurar_sensor():
    """
    Endpoint para configurar parâmetros do sensor para ajudar na depuração
    """
    invert = request.args.get('inverter', default=None)
    pullup = request.args.get('pullup', default=None)
    
    with contador._state_lock:
        if invert is not None:
            contador.invert_logic = invert.lower() in ['true', '1', 't', 'y', 'yes']
        
        if pullup is not None:
            old_pullup = contador.pullup
            contador.pullup = pullup.lower() in ['true', '1', 't', 'y', 'yes']
            
            # Se o pullup mudou, precisamos reinicializar o sensor
            if old_pullup != contador.pullup:
                contador.inicializar_sensor()
        
        # Reset do Flop para garantir início correto
        contador.Flop = False
        
        # Responder com a configuração atual
        return jsonify({
            "status": "success", 
            "message": "Configuração do sensor atualizada",
            "configuracao": {
                "invert_logic": contador.invert_logic,
                "pullup": contador.pullup,
                "flop_state": contador.Flop,
                "sensor_pin": contador.SENSOR_PIN
            }
        }), 200

@app.route("/teste-incremento", methods=["GET"])
@log_exceptions
def teste_incremento():
    """
    Incrementa manualmente a contagem para depuração
    """
    try:
        if contador.EstadoContador == 1 and not contador.EstadoPausa:
            with contador._contagem_lock:
                old_count = contador.ContagemAtual
                contador.increment_count()
                new_count = contador.ContagemAtual
                
                return jsonify({
                    "status": "success", 
                    "message": f"Contagem incrementada manualmente: {old_count} -> {new_count}",
                    "count_before": old_count,
                    "count_after": new_count
                }), 200
        else:
            return jsonify({
                "status": "error", 
                "message": "Não é possível incrementar: contador não está em modo de contagem ou está pausado",
                "estado_contador": contador.EstadoContador,
                "pausa": contador.EstadoPausa
            }), 400
    except Exception as e:
        logging.error(f"Erro ao incrementar contagem: {e}")
        return jsonify({"status": "error", "message": f"Erro ao incrementar contagem: {e}"}), 500

@app.route("/sensor-info", methods=["GET"])
@log_exceptions
def sensor_info():
    """
    Retorna informações de diagnóstico sobre o sensor
    """
    try:
        # Ler o estado atual do sensor
        sensor_state = None
        if contador.sensor_initialized:
            sensor_state = GPIO.input(contador.SENSOR_PIN)
        
        info = {
            "estado_atual": sensor_state,
            "flop": contador.Flop,
            "pullup": contador.pullup,
            "invert_logic": contador.invert_logic,
            "pin": contador.SENSOR_PIN,
            "last_error": contador.read_error_count,
            "sensor_initialized": contador.sensor_initialized,
            "estado_contador": contador.EstadoContador,
            "pausa": contador.EstadoPausa,
            "contagem_atual": contador.ContagemAtual
        }
        
        return jsonify({"status": "success", "info": info}), 200
    except Exception as e:
        logging.error(f"Erro ao obter informações do sensor: {e}")
        return jsonify({"status": "error", "message": f"Erro ao obter informações: {e}"}), 500

if __name__ == "__main__":
    try:
        # Registrar limpeza de GPIO no encerramento
        atexit.register(GPIO.cleanup)
        
        # Inicializar sistema
        init_main()
        
        # Configurar contexto SSL para HTTPS
        try:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(certfile="CERT.crt", keyfile="CERT.key")
            app.run(host="0.0.0.0", port=443, ssl_context=context, threaded=True)
        except Exception as ssl_error:
            logging.error(f"Erro ao iniciar servidor HTTPS: {ssl_error}")
            # Fallback para HTTP em caso de erro SSL
            logging.warning("Iniciando em modo HTTP (sem SSL) como fallback")
            app.run(host="0.0.0.0", port=8080, threaded=True)
    except KeyboardInterrupt:
        logging.info("Servidor encerrado por interrupção do teclado")
        GPIO.cleanup()
    except Exception as e:
        logging.critical(f"Erro fatal: {e}")
        logging.critical(traceback.format_exc())
        GPIO.cleanup()

#!/bin/bash

# Verifica se está a ser executado como root
if [ "$EUID" -ne 0 ]; then
  echo "Este script deve ser executado como root (sudo)"
  exit 1
fi

# Diretório de instalação
INSTALL_DIR="/home/pi/krones"

# Verifica se o script setup_raspberry.sh foi executado
if [ ! -d "$INSTALL_DIR/venv" ]; then
  echo "ERRO: Ambiente virtual não encontrado. Execute primeiro o script setup_raspberry.sh."
  exit 1
fi

# Cria diretório se não existir
mkdir -p $INSTALL_DIR

# Copia todos os ficheiros para o diretório de instalação
cp -f main.py $INSTALL_DIR/
cp -f requirements.txt $INSTALL_DIR/
cp -f CERT.crt $INSTALL_DIR/
cp -f CERT.key $INSTALL_DIR/
cp -f README.md $INSTALL_DIR/

# Ativa ambiente virtual e instala dependências
echo "A instalar dependências no ambiente virtual..."
source $INSTALL_DIR/venv/bin/activate
pip install -r $INSTALL_DIR/requirements.txt
deactivate

# Copia ficheiro de serviço para systemd
cp -f krones-contador.service /etc/systemd/system/

# Define permissões corretas
chmod 644 /etc/systemd/system/krones-contador.service
chmod +x $INSTALL_DIR/main.py
chown -R pi:pi $INSTALL_DIR

# Recarrega serviços do systemd
systemctl daemon-reload

# Ativa o serviço para iniciar no arranque
systemctl enable krones-contador.service

# Inicia o serviço
systemctl start krones-contador.service

echo "Instalação concluída. O serviço está a executar e vai iniciar automaticamente no arranque."
echo "Para verificar o estado do serviço: sudo systemctl status krones-contador"
echo "Para ver os logs: sudo journalctl -u krones-contador -f" 
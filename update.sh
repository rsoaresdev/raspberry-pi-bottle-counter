#!/bin/bash

# Verifica se está a ser executado como root
if [ "$EUID" -ne 0 ]; then
  echo "Este script deve ser executado como root (sudo)"
  exit 1
fi

# Diretório de instalação
INSTALL_DIR="/home/pi/krones"

# Verifica se o ambiente virtual existe
if [ ! -d "$INSTALL_DIR/venv" ]; then
  echo "ERRO: Ambiente virtual não encontrado. Execute primeiro o script setup_raspberry.sh."
  exit 1
fi

# Para o serviço
echo "A parar o serviço..."
systemctl stop krones-contador.service

# Faz backup dos ficheiros de estado (caso existam)
if [ -f "$INSTALL_DIR/contador_state.backup" ]; then
  cp -f $INSTALL_DIR/contador_state.backup $INSTALL_DIR/contador_state.backup.old
  echo "Backup do estado guardado."
fi

# Copia os novos ficheiros para o diretório de instalação
echo "A atualizar ficheiros..."
cp -f main.py $INSTALL_DIR/
cp -f requirements.txt $INSTALL_DIR/
cp -f README.md $INSTALL_DIR/

# Atualiza certificados apenas se fornecidos
if [ -f "CERT.crt" ] && [ -f "CERT.key" ]; then
  cp -f CERT.crt $INSTALL_DIR/
  cp -f CERT.key $INSTALL_DIR/
  echo "Certificados atualizados."
fi

# Atualiza serviço systemd se fornecido
if [ -f "krones-contador.service" ]; then
  cp -f krones-contador.service /etc/systemd/system/
  chmod 644 /etc/systemd/system/krones-contador.service
  systemctl daemon-reload
  echo "Serviço systemd atualizado."
fi

# Atualiza dependências no ambiente virtual
echo "A atualizar dependências..."
source $INSTALL_DIR/venv/bin/activate
pip install -r $INSTALL_DIR/requirements.txt
deactivate

# Define permissões corretas
chmod +x $INSTALL_DIR/main.py
chown -R pi:pi $INSTALL_DIR

# Reinicia o serviço
echo "A reiniciar o serviço..."
systemctl restart krones-contador.service

echo "Atualização concluída. O serviço foi reiniciado."
echo "Para verificar o estado: sudo systemctl status krones-contador" 
#!/bin/bash

# Verifica se está a ser executado como root
if [ "$EUID" -ne 0 ]; then
  echo "Este script deve ser executado como root (sudo)"
  exit 1
fi

echo "===== Configuração Inicial do Sistema Krones ====="
echo "A atualizar o sistema..."
apt update && apt upgrade -y

echo "A instalar dependências básicas do sistema..."
apt install -y python3 python3-pip python3-venv python3-dev build-essential libssl-dev libffi-dev

echo "A instalar dependências para GPIO e base de dados..."
apt install -y freetds-dev freetds-bin

# Garantir que as permissões de GPIO estão corretas
echo "A configurar permissões GPIO para o usuário pi..."
usermod -a -G gpio pi
usermod -a -G dialout pi

echo "A criar ambiente virtual Python..."
mkdir -p /home/pi/krones
cd /home/pi/krones
python3 -m venv venv
source venv/bin/activate

echo "A instalar dependências Python no ambiente virtual..."
pip3 install --upgrade pip
pip3 install flask numpy pymssql RPi.GPIO

echo "A criar arquivo de serviço..."
cat > /etc/systemd/system/krones-contador.service << EOL
[Unit]
Description=Krones Contador de Garrafas
After=network.target

[Service]
User=pi
WorkingDirectory=/home/pi/krones
ExecStart=/home/pi/krones/venv/bin/python /home/pi/krones/main.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOL

# Recarregar configurações do systemd
systemctl daemon-reload
systemctl enable krones-contador.service

echo "Ambiente preparado com sucesso!"
echo "Execute o script install.sh a seguir para instalar o aplicativo." 
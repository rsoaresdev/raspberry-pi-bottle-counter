# Sistema de Contagem de Garrafas Krones

## Descrição
Sistema de contagem de garrafas com sensores infravermelhos e pistão para controlo de porta, executado em Raspberry Pi.

## Melhorias Implementadas

### Robustez e Estabilidade
- Mecanismo de recuperação de estado após reinicialização inesperada
- Proteção contra falsos positivos na contagem 
- Tratamento de exceções para operações críticas
- Sistema de reconexão automática à base de dados
- Reinicialização segura de GPIO em caso de falhas
- Backup periódico do estado atual
- Compatibilidade com Raspberry Pi 64-bit

### Performance e Segurança
- Proteção de threads com locks
- Operações de BD não-bloqueantes usando threads independentes
- Limitação de tamanho das listas de estatísticas para evitar vazamento de memória
- Validação robusta dos dados antes de processamento
- Gestão de GPIO baseada em eventos usando RPi.GPIO (em vez de polling)

### Melhorias Funcionais
- API para histórico de ordens antiga melhorada
- Estatísticas com médias móveis para maior precisão
- Tempo de resposta melhorado
- Proteção contra reinicializações frequentes

## Estrutura do Sistema
- **main.py**: Aplicação principal
- **requirements.txt**: Dependências do projeto
- **CERT.crt/CERT.key**: Certificados SSL para conexão segura
- **contador_state.backup**: Ficheiro automático de backup de estado
- **setup_raspberry.sh**: Script para preparação inicial do Raspberry Pi
- **install.sh**: Script de instalação como serviço
- **update.sh**: Script para atualização do sistema
- **krones-contador.service**: Definição do serviço systemd

## Requisitos
```
Python 3.7+
Flask
NumPy
pymssql
RPi.GPIO
```

## Instalação em Raspberry Pi 64-bit

### 1. Configuração Inicial do Sistema
```bash
# Copie todos os ficheiros para o Raspberry Pi
# Dê permissão de execução aos scripts
chmod +x setup_raspberry.sh install.sh update.sh

# Execute o script de preparação inicial
sudo ./setup_raspberry.sh
```

### 2. Instalação do Serviço
```bash
# Execute o script de instalação após a configuração inicial
sudo ./install.sh
```

### 3. Verificação da Instalação
```bash
# Verificar estado do serviço
sudo systemctl status krones-contador

# Ver logs em tempo real
sudo journalctl -u krones-contador -f
```

## Atualização do Sistema
Para atualizar o software para uma nova versão:
```bash
sudo ./update.sh
```

## Gestão do Serviço
- **Ver estado**: `sudo systemctl status krones-contador`
- **Reiniciar**: `sudo systemctl restart krones-contador`
- **Parar**: `sudo systemctl stop krones-contador`
- **Iniciar**: `sudo systemctl start krones-contador`
- **Ver logs**: `sudo journalctl -u krones-contador -f`

## Tabelas da Base de Dados
- **krones_contadoreslinha**: Registo das ordens de produção
- **krones_contadoreslinhacontagem**: Registos de contagem
- **krones_historico_contagem**: Dados históricos e estatísticas

## Configuração para Desenvolvimento

### Credenciais e Configurações Sensíveis
Antes de usar este código, precisa de configurar as seguintes informações:

1. No ficheiro `main.py`, procure pela classe `Contador` e substitua:
   - `your_db_server` pelo endereço do seu servidor da base de dados
   - `your_db_user` pelo nome de utilizador da base de dados
   - `your_db_password` pela palavra-passe da base de dados
   - `your_db_name` pelo nome da base de dados
   
2. Para CORS, atualize a lista `allowed_origins` com os domínios permitidos para aceder à API.

3. Substitua os ficheiros `CERT.key` e `CERT.crt` por certificados SSL válidos para o seu ambiente. 

### Tabelas da Base de Dados Necessárias
O sistema requer as seguintes tabelas SQL Server:

```sql
CREATE TABLE krones_contadoreslinha (
    Id INT IDENTITY(1,1) PRIMARY KEY,
    Ordem VARCHAR(50) NOT NULL,
    Artigo VARCHAR(50),
    DescricaoArtigo VARCHAR(255),
    CadenciaArtigo INT,
    ContadorObjetivo INT DEFAULT 0,
    Abertura DATETIME,
    Fecho DATETIME NULL
);

CREATE TABLE krones_contadoreslinhacontagem (
    Id INT IDENTITY(1,1) PRIMARY KEY,
    IdContagem INT NOT NULL,
    ContagemAtual INT NOT NULL,
    Objetivo INT NOT NULL,
    DataLeitura DATETIME NOT NULL
);

CREATE TABLE krones_historico_contagens (
    Id INT IDENTITY(1,1) PRIMARY KEY,
    DataDados DATETIME NOT NULL,
    Ordem VARCHAR(50) NOT NULL,
    Artigo VARCHAR(50),
    DescricaoArtigo VARCHAR(255),
    CadenciaArtigo INT,
    Inicio DATETIME NULL,
    Fim DATETIME NULL,
    ContagemAtual INT,
    ContagemTotal INT,
    MediaProducao FLOAT,
    EstimativaFecho DATETIME NULL,
    Paragens INT NULL,
    Quebras INT NULL,
    EstadoPorta BIT,
    EstadoContador INT,
    EstadoConfiguracao BIT,
    Nominal FLOAT NULL,
    Media FLOAT NULL,
    Cadencia FLOAT NULL,
    Tempo VARCHAR(20) NULL
);
```

## Endpoints API
- **/abrir-porta**: Abre a porta
- **/fechar-porta**: Fecha a porta
- **/iniciar-contagem**: Inicia a contagem
- **/parar-contagem**: Para a contagem
- **/pausa**: Pausa a contagem
- **/retomar**: Retoma a contagem
- **/quebra/{valor}**: Regista quebras
- **/setup/{ordem}/{cnt}**: Configura nova contagem
- **/reset-contador**: Reset completo do contador
- **/status**: Retorna estado atual
- **/api/info**: Retorna dados históricos
- **/api/info/{NumPontos}/{Ordem}**: Retorna dados históricos filtrados 
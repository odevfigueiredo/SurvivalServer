# Survival Server — Guia de instalação e operação

## O que é

Servidor de biblioteca e arquivos para **rede local sem internet**, acessível pelo navegador de vários dispositivos. Não é um APK: o servidor roda em **Windows, Linux ou Raspberry Pi**, e os celulares usam o navegador Android/iOS conectado ao mesmo Wi-Fi local.

### Incluído

- Portal responsivo em português, tema escuro/vermelho, sem CDN e sem depender de serviços externos.
- Login por senha: administrador (`admin`) e leitor (`familia`, opcional).
- Upload de arquivos, importação no computador, download e visualização de PDFs, imagens, áudio e vídeos compatíveis.
- Pesquisa por nome, descrição e pelo texto de `.txt`, `.md`, `.csv`, `.json`, `.log`.
- Biblioteca organizada em categorias: Documentos, Manuais, Mapas, Vídeos, Wikipédia/ZIM, Guias, Outros.
- Streaming HTTP com suporte a requisições `Range` para mídias.
- SQLite local, senhas com PBKDF2-HMAC-SHA256, proteção CSRF, limite de tentativas de login.
- Backup completo em ZIP, restauração segura e validação SHA-256 dos arquivos.
- Arquivos de exemplo de planejamento familiar.

**Não incluído nesta versão:** hospedagem na internet, HTTPS automático, leitura de artigos ZIM dentro do próprio portal, IA, GPS, modo mesh, sincronização entre servidores, criptografia dos backups. Não é substituto do Arca Offline para documentos sensíveis.

## Requisitos

- Python 3.10 ou mais recente.
- Um PC, notebook ou Raspberry Pi ligado, com armazenamento suficiente.
- Roteador Wi-Fi local ou ponto de acesso que permita comunicação entre dispositivos. O roteador **não precisa estar conectado à internet**.
- Navegador Chrome, Firefox, Edge ou similar no celular ou PC.

## Windows 10/11

1. Instale o Python pelo site oficial antes da emergência: https://www.python.org/downloads/windows/
2. Extraia o ZIP do projeto.
3. Dê dois cliques em **`iniciar_windows.bat`** e crie uma senha de administrador de pelo menos 10 caracteres. Crie também uma senha diferente para a conta de leitura `familia`.
4. O terminal imprimirá o endereço de acesso local, por exemplo **`http://192.168.1.10:8765`**.
5. Conecte os celulares ao **mesmo Wi-Fi** do computador e abra o endereço pelo navegador. Na aba **Acesso na rede**, o portal também mostra a URL e um QR para compartilhar com outros aparelhos.
6. Se o firewall do Windows solicitar autorização, permita **apenas em redes Privadas**, não em redes Públicas.

Se preferir executar manualmente no terminal dentro da pasta do projeto:

```powershell
python server.py init
python server.py run --lan
```

## Linux ou Raspberry Pi OS

```bash
cd /caminho/SurvivalServer
chmod +x iniciar_linux.sh
./iniciar_linux.sh
```

Também é possível executar:

```bash
python3 server.py init
python3 server.py run --lan
```

A porta padrão é **8765**. Para outra porta:

```bash
python3 server.py run --lan --port 9123
```

### Sobre o Wi-Fi sem internet

Você pode utilizar um roteador doméstico ligado apenas à energia elétrica (sem cabo de internet), um roteador de viagem ou um ponto de acesso local no computador. Se usar hotspot de celular, valide que os clientes conseguem trocar dados entre si: alguns modelos isolam dispositivos.

No Linux com NetworkManager, algumas placas Wi-Fi compatíveis com modo AP podem criar um hotspot com `nmcli`. Como a sintaxe varia entre versões/placas, configure e teste isso enquanto há internet disponível. Não é necessário ter internet para que aparelhos conectados ao mesmo AP acessem o servidor.

## Utilização

Entre como `admin` para adicionar e excluir arquivos. Entre como `familia` (se configurada) para buscar e baixar.

Administradores podem criar contas de leitura ou de administração na aba **Contas**. A troca de senha e a exclusão encerram as sessões daquela conta em todos os aparelhos. A troca da própria senha exige um novo login.

### Importar um ZIM grande

Arquivos Wikipédia em ZIM podem ocupar gigabytes. Importar diretamente do armazenamento do servidor evita o limite de upload do navegador:

```bash
python server.py import-file "/caminho/Wikipedia_pt.zim" --category "Wikipedia/ZIM" --description "Enciclopédia offline"
```

No Windows:

```powershell
python server.py import-file "D:\\Enciclopedias\\Wikipedia_pt.zim" --category "Wikipedia/ZIM"
```

Para **navegar nos artigos ZIM diretamente no navegador**, instale o utilitário independente **Kiwix-serve** e execute `kiwix-serve --port=8080 /pasta/dos/arquivos_zim`. A partir de outro celular na rede, acesse `http://IP_DO_SERVIDOR:8080`. O Survival Server apenas armazena e oferece os arquivos ZIM para download, não interpreta seu conteúdo internamente.

Referência: https://kiwix-tools.readthedocs.io/en/latest/kiwix-serve.html

### Backups e conferência

Com o terminal aberto **em outra janela** (ou com o servidor parado):

```bash
python server.py verify
python server.py backup
```

O `backup` salva um ZIP com o banco e os arquivos do catálogo. O ZIP deve ficar fora da pasta de dados. Durante a cópia, as consultas continuam disponíveis e as gravações aguardam. **Esse ZIP não é criptografado.** Use o Backup Guardian para proteger uma cópia em outro dispositivo.

Restauração com servidor **desligado**:

```bash
python server.py restore /caminho/SurvivalServer_Backup_2026XXXX.zip --yes
```

A restauração confere os arquivos contra o catálogo antes da troca e preserva uma pasta anterior com o sufixo `.anterior_...`, quando houver dados existentes. Em uma instalação nova, pode ser executada antes de `init`.

### Novos usuários

```bash
python server.py add-user joao --role leitor
python server.py add-user supervisora --role admin
```

### Armazenamento em SSD externo

É possível selecionar uma pasta de dados externa **com o parâmetro global antes do subcomando**:

```bash
python server.py --data /media/ssd/survival init
python server.py --data /media/ssd/survival run --lan
```

As ações `backup`, `verify`, `import-file` e `restore` também aceitam `--data` antes do subcomando.

## Segurança e limitações

- O aplicativo usa HTTP, **não HTTPS**. Em uma rede local, outras pessoas que consigam observar o tráfego podem visualizar logins e arquivos. Use apenas redes privadas confiáveis, com WPA2/WPA3. Não compartilhe informações confidenciais, senhas pessoais, cartões ou prontuários.
- **Não abra a porta 8765 na internet**, não configure port forwarding e não coloque o servidor em uma DMZ.
- Este é um protótipo para uso familiar em rede local, **não foi auditado profissionalmente** e utiliza a biblioteca `http.server`, que a documentação Python não recomenda para servidores públicos/produção.
- A senha `familia` pode ser compartilhada com os leitores, porém prefira usuários distintos quando necessário.
- O servidor precisa estar **ligado** e conectado à rede local para os aparelhos acessarem seus arquivos. Planeje bateria e energia para o roteador e para o servidor.
- O site não precisa de internet, não contém anúncios, analytics ou bibliotecas externas.
- Arquivos ZIP de backup são cópias comuns e podem ser lidos por quem obtiver acesso a eles.
- Antes de depender do sistema para uma emergência, **teste sem internet, com o modo avião e em mais de um aparelho**. Use também versões impressas de informações críticas.

## Testes

```bash
python -m unittest discover -s tests -v
```

O servidor e o portal são exemplos funcionais e pontos de partida para o Codex. Não prometem segurança absoluta, redundância automática ou alta disponibilidade.

## Alternativa: usar um Android como servidor com Termux

Opcionalmente, o aplicativo Termux pode executar Python no próprio Android, mas instale o Termux e o Python **antes** de ficar sem internet.

```bash
pkg install python
termux-setup-storage
cd ~/storage/downloads/SurvivalServer
python server.py init
python server.py run --lan
```

Se o diretório extraído tiver outro nome, ajuste o caminho. Ao rodar no Android, o próprio celular pode abrir `http://127.0.0.1:8765` no navegador. Para outros dispositivos acessarem, conecte-os à mesma rede Wi-Fi/hotspot e teste o endereço IP do aparelho. Restrições de hotspot, permissão de rede e economia de bateria variam por aparelho. O Termux pode ser encerrado pelo Android em segundo plano; mantenha a sessão ativa e faça testes reais.

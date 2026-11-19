# Como usar o Survival Server sem internet

O Survival Server é um portal de arquivos para sua casa ou seu grupo familiar. Ele roda em um notebook, PC ou Raspberry Pi ligado, conectando os navegadores dos dispositivos pela mesma rede Wi-Fi local.

## Primeira execução
1. Instale Python 3.10 ou superior no computador.
2. Extraia o projeto para uma pasta local.
3. Execute `python server.py init` e defina senhas diferentes para `admin` e `familia`.
4. Execute `python server.py run --lan`.
5. No celular, conectado ao mesmo Wi-Fi, visite `http://IP_DO_SERVIDOR:8765`.
6. Entre com a conta `familia` para consultar e baixar arquivos.

## Guardar e verificar
- `python server.py import-file minha_enciclopedia.zim --category Wikipedia/ZIM`
- `python server.py backup`
- `python server.py verify`

## Limitações importantes
- Não transmite internet para os aparelhos: só os conteúdos da biblioteca.
- Exige energia elétrica no computador e na rede local.
- Não é um serviço de nuvem nem um servidor para expor à internet.
- O tráfego HTTP na rede local não é criptografado.
- Arquivos ZIM podem ser baixados aqui; para navegar na Wikipédia no browser, use também o Kiwix-serve instalado separadamente.

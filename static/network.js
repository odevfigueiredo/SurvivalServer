'use strict';

(() => {
  const mount = document.querySelector('#networkAccessDynamic');
  if (!mount) return;

  let pendingRequest;

  function element(tag, className, content) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (content) node.textContent = content;
    return node;
  }

  function isLoopback(url) {
    const host = new URL(url).hostname;
    return host === 'localhost' || host === '[::1]' || host.startsWith('127.');
  }

  async function copyAddress(input, button, feedback) {
    if (navigator.clipboard?.writeText) {
      try {
        await navigator.clipboard.writeText(input.value);
        feedback.textContent = 'Endereço copiado.';
        return;
      } catch {
        // HTTP na rede local pode exigir a seleção do campo.
      }
    }

    input.focus({preventScroll: true});
    input.select();
    input.setSelectionRange(0, input.value.length);

    let copied = false;
    try {
      copied = document.execCommand('copy');
    } catch {
      // O campo selecionado permanece disponível para copiar manualmente.
    }

    if (copied) {
      button.focus({preventScroll: true});
      feedback.textContent = 'Endereço copiado.';
    } else {
      feedback.textContent = 'Endereço selecionado. Use a opção Copiar do seu dispositivo.';
    }
  }

  function addressField(id, label, value) {
    const field = element('div', 'network-address-field');
    const heading = element('h2', 'network-address-title');
    const title = element('label', '', label);
    const row = element('div', 'network-address-row');
    const input = element('input', 'network-address-value');
    const button = element('button', 'network-copy', 'Copiar');
    const feedback = element('span', 'network-copy-feedback');

    input.id = id + '-url';
    input.type = 'text';
    input.readOnly = true;
    input.value = value;
    input.spellcheck = false;
    title.htmlFor = input.id;
    button.type = 'button';
    button.id = id + '-copy';
    button.setAttribute('aria-label', 'Copiar ' + label.toLocaleLowerCase('pt-BR'));
    feedback.setAttribute('role', 'status');
    button.addEventListener('click', () => copyAddress(input, button, feedback));

    heading.append(title);
    row.append(input, button);
    field.append(heading, row, feedback);
    return {field, input, feedback};
  }

  function addQr(figure, canvas, url) {
    figure.hidden = true;
    let matrix;
    try {
      matrix = window.survivalQrMatrix?.(url);
    } catch {
      return;
    }
    if (!Array.isArray(matrix) || matrix.length === 0) return;

    const context = canvas.getContext('2d');
    if (!context) return;

    const quietZone = 4;
    const moduleSize = 8;
    const side = (matrix.length + quietZone * 2) * moduleSize;
    canvas.width = side;
    canvas.height = side;
    context.fillStyle = '#fff';
    context.fillRect(0, 0, side, side);
    context.fillStyle = '#000';
    matrix.forEach((row, y) => {
      row.forEach((dark, x) => {
        if (dark) {
          context.fillRect((x + quietZone) * moduleSize, (y + quietZone) * moduleSize, moduleSize, moduleSize);
        }
      });
    });
    figure.hidden = false;
  }

  function lanAddresses(result) {
    if (!result?.lan_enabled || !Array.isArray(result.access_urls)) return [];

    const addresses = new Set();
    for (const address of result.access_urls) {
      if (address?.kind !== 'lan' || typeof address.url !== 'string') continue;
      try {
        const url = new URL(address.url);
        if (url.protocol === location.protocol && !isLoopback(url.origin)) {
          addresses.add(url.origin);
        }
      } catch {
        // Ignora entradas que não são endereços utilizáveis.
      }
    }
    return [...addresses];
  }

  function shareCard(addresses) {
    const card = element('section', 'network-panel network-share-card');
    const initial = addresses.includes(location.origin) ? location.origin : addresses[0];
    const address = addressField('network-share', 'Endereço para outros dispositivos', initial);
    const instructions = element('p', 'network-hint',
      'Abra este endereço no navegador do outro aparelho.');
    const content = element('div', 'network-share-content');
    const figure = element('figure', 'network-qr');
    const canvas = element('canvas', 'network-qr-image');
    const caption = element('figcaption', 'network-qr-caption', 'Escaneie com a câmera');

    canvas.id = 'network-share-qr';
    canvas.setAttribute('role', 'img');
    canvas.setAttribute('aria-label', 'Código QR do endereço para outros dispositivos');
    figure.append(canvas, caption);

    content.append(address.field, instructions);
    if (addresses.length > 1) {
      const interfaceField = element('div', 'network-interface');
      const label = element('label', 'network-field-label', 'Outros endereços da rede');
      const select = element('select', 'network-address-value');
      select.id = 'network-lan-interface';
      label.htmlFor = select.id;
      for (const url of addresses) {
        const option = element('option', '', url);
        option.value = url;
        select.append(option);
      }
      select.value = initial;
      select.addEventListener('change', () => {
        address.input.value = select.value;
        address.feedback.textContent = '';
        addQr(figure, canvas, select.value);
      });
      interfaceField.append(label, select);
      content.append(interfaceField);
    }
    addQr(figure, canvas, initial);
    card.append(content, figure);
    return card;
  }

  function sessionCard() {
    const card = element('section', 'network-panel network-session-card');
    const address = addressField('network-session', 'Endereço desta sessão', location.origin);
    const description = isLoopback(location.origin)
      ? 'Este endereço abre o portal somente neste computador.'
      : 'Este é o endereço que seu navegador está usando agora.';
    card.append(address.field, element('p', 'network-hint', description));
    return card;
  }

  function stateCard(title, description, retry) {
    const card = element('section', 'network-panel network-state-card');
    card.id = 'network-access-state';
    card.append(
      element('h2', 'network-address-title', title),
      element('p', 'network-hint', description),
    );
    if (retry) {
      const button = element('button', 'network-copy network-retry', 'Atualizar endereços');
      button.type = 'button';
      button.addEventListener('click', loadAccess);
      card.append(button);
    }
    return card;
  }

  function render(share) {
    mount.classList.add('network-access');
    mount.replaceChildren(share, sessionCard());
  }

  async function loadAccess() {
    pendingRequest?.abort();
    const request = new AbortController();
    pendingRequest = request;
    mount.setAttribute('aria-busy', 'true');
    render(stateCard('Endereço para outros dispositivos', 'Consultando a rede local…'));

    try {
      const result = await api('/api/access', {signal: request.signal});
      if (request.signal.aborted) return;

      const addresses = lanAddresses(result);
      if (addresses.length) {
        render(shareCard(addresses));
      } else if (result?.lan_enabled) {
        render(stateCard('Nenhum endereço de rede encontrado',
          'Conecte o servidor à mesma rede dos outros aparelhos e atualize os endereços.', true));
      } else {
        const card = stateCard('Acesso apenas neste computador',
          'Para abrir o portal em outros aparelhos, reinicie o servidor com o comando abaixo e conecte os aparelhos ao mesmo Wi-Fi.');
        card.append(element('code', 'network-command', 'python server.py run --lan'));
        render(card);
      }
    } catch {
      if (!request.signal.aborted) {
        render(stateCard('Não foi possível consultar a rede',
          'Atualize os endereços para tentar novamente.', true));
      }
    } finally {
      if (pendingRequest === request) {
        pendingRequest = undefined;
        mount.removeAttribute('aria-busy');
      }
    }
  }

  document.addEventListener('survival:network-open', loadAccess);
  document.addEventListener('survival:logout', () => {
    pendingRequest?.abort();
    pendingRequest = undefined;
    mount.replaceChildren();
    mount.classList.remove('network-access');
    mount.removeAttribute('aria-busy');
  });
})();

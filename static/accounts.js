'use strict';

(() => {
  const list = $('#accountRows');
  const createForm = $('#accountsCreate');
  const passwordForm = $('#accountPasswordForm');
  const dialog = $('#accountPasswordDialog');
  const refreshButton = $('#reloadAccounts');
  let listRequest;
  let passwordAccount;

  function setStatus(message) {
    const status = $('#accountsStatus');
    status.textContent = message;
    status.hidden = !message;
  }

  function renderAccount(account) {
    const ownAccount = account.username === session.username;
    const row = text('li', 'account-row', '');
    const initials = text('span', 'account-initials', account.username.slice(0, 2).toUpperCase());
    initials.setAttribute('aria-hidden', 'true');
    const info = text('div', 'account-info', '');
    info.append(text('strong', '', account.username), text('small', '',
      `${account.role === 'admin' ? 'Administrador' : 'Leitor'}${ownAccount ? ' · Sua conta' : ''}`));
    const actions = text('div', 'account-actions', '');
    const passwordButton = btn('Alterar senha', () => openPassword(account), 'secondary-button');
    passwordButton.setAttribute('aria-label', `Alterar senha de ${account.username}`);
    actions.append(passwordButton);
    if (!ownAccount) {
      const deleteButton = btn('Excluir', () => removeAccount(account, deleteButton), 'secondary-button account-delete');
      deleteButton.setAttribute('aria-label', `Excluir ${account.username}`);
      actions.append(deleteButton);
    }
    row.append(initials, info, actions);
    return row;
  }

  async function loadAccounts() {
    if (session?.role !== 'admin') return;
    listRequest?.abort();
    const request = new AbortController();
    listRequest = request;
    list.setAttribute('aria-busy', 'true');
    refreshButton.disabled = true;
    try {
      const {users} = await api('/api/users', {signal: request.signal});
      if (listRequest !== request) return;
      list.replaceChildren(...users.map(renderAccount));
    } catch (error) {
      if (!request.signal.aborted) setStatus(error.message);
    } finally {
      if (listRequest === request) {
        listRequest = null;
        list.setAttribute('aria-busy', 'false');
        refreshButton.disabled = false;
      }
    }
  }

  async function createAccount(event) {
    event.preventDefault();
    const submit = createForm.querySelector('[type=submit]');
    if (submit.disabled) return;
    const errorMessage = $('#accountCreateError');
    const fields = new FormData(createForm);
    const account = Object.fromEntries(fields);
    account.username = account.username.trim();
    setStatus('');
    errorMessage.hidden = true;
    submit.disabled = true;
    try {
      await api('/api/users', {method:'POST', json:account});
      createForm.reset();
      setStatus(`Conta ${account.username} criada.`);
      await loadAccounts();
      $('#accountUsername').focus();
    } catch (error) {
      errorMessage.textContent = error.message;
      errorMessage.hidden = false;
    } finally {
      submit.disabled = false;
    }
  }

  function openPassword(account) {
    passwordAccount = account.username;
    $('#accountPasswordTitle').textContent = `Alterar senha de ${account.username}`;
    $('#accountPasswordError').hidden = true;
    passwordForm.reset();
    dialog.showModal();
    $('#newAccountPassword').focus();
  }

  async function changePassword(event) {
    event.preventDefault();
    const submit = passwordForm.querySelector('[type=submit]');
    if (submit.disabled || !passwordAccount) return;
    const user = passwordAccount;
    const errorMessage = $('#accountPasswordError');
    errorMessage.hidden = true;
    submit.disabled = true;
    $('#cancelAccountPassword').disabled = true;
    try {
      const result = await api(`/api/users/${encodeURIComponent(user)}/password`, {
        method:'PUT', json:{password:$('#newAccountPassword').value},
      });
      dialog.close();
      if (result.reauthenticate) {
        session = null;
        showAuth();
        $('#loginUsername').value = user;
        $('#loginPassword').focus();
        toast('Senha alterada. Entre novamente.');
      } else {
        setStatus(`Senha de ${user} alterada. As sessões anteriores foram encerradas.`);
      }
    } catch (error) {
      errorMessage.textContent = error.message;
      errorMessage.hidden = false;
    } finally {
      submit.disabled = false;
      $('#cancelAccountPassword').disabled = false;
    }
  }

  async function removeAccount(account, button) {
    if (!confirm(`Excluir a conta “${account.username}” e encerrar suas sessões?`)) return;
    button.disabled = true;
    try {
      await api(`/api/users/${encodeURIComponent(account.username)}`, {method:'DELETE'});
      setStatus(`Conta ${account.username} excluída.`);
      await loadAccounts();
      refreshButton.focus();
    } catch (error) {
      setStatus(error.message);
    } finally {
      button.disabled = false;
    }
  }

  refreshButton.addEventListener('click', () => {setStatus(''); loadAccounts();});
  createForm.addEventListener('submit', createAccount);
  passwordForm.addEventListener('submit', changePassword);
  $('#cancelAccountPassword').addEventListener('click', () => dialog.close());
  dialog.addEventListener('cancel', event => {
    if ($('#cancelAccountPassword').disabled) event.preventDefault();
  });
  dialog.addEventListener('close', () => {passwordAccount = null; passwordForm.reset();});
  document.addEventListener('survival:accounts-open', loadAccounts);
  document.addEventListener('survival:logout', () => {
    listRequest?.abort();
    listRequest = null;
    list.replaceChildren();
    list.setAttribute('aria-busy', 'false');
    refreshButton.disabled = false;
    createForm.reset();
    $('#accountCreateError').hidden = true;
    if (dialog.open) dialog.close();
    setStatus('');
  });
})();

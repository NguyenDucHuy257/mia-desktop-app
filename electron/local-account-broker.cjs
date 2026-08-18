const { randomUUID } = require('node:crypto');
const { runBrokerCommand, validateConnectionId, validateCredentials } = require('./account-connection-broker.cjs');

function createLocalAccountBroker(getRuntime, protector, now = () => new Date().toISOString(), createId = randomUUID) {
  if (typeof getRuntime !== 'function' || !protector?.encrypt) throw new TypeError('Invalid local account dependencies.');
  const encodePassword = (password) => protector.encrypt(password).toString('base64');
  return Object.freeze({
    create: (credentials) => runBrokerCommand(async () => {
      const valid = validateCredentials(credentials);
      return getRuntime().invoke('accounts.create', {
        account_id: createId(), tax_code: valid.username,
        encrypted_password: encodePassword(valid.password), timestamp: now(),
      });
    }),
    list: () => runBrokerCommand(() => getRuntime().invoke('accounts.list')),
    get: (accountId) => runBrokerCommand(() => getRuntime().invoke('accounts.get', { account_id: validateConnectionId(accountId) })),
    reconnect: (accountId, credentials) => runBrokerCommand(() => {
      const valid = validateCredentials(credentials);
      return getRuntime().invoke('accounts.update', {
        account_id: validateConnectionId(accountId), tax_code: valid.username,
        encrypted_password: encodePassword(valid.password), timestamp: now(),
      });
    }),
    revoke: (accountId) => runBrokerCommand(async () => {
      await getRuntime().invoke('accounts.delete', { account_id: validateConnectionId(accountId) });
      return null;
    }),
  });
}

module.exports = { createLocalAccountBroker };

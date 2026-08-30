const { runBrokerCommand, validateConnectionId, validateCredentials } = require('./account-connection-broker.cjs');

function createLocalAccountBroker(getRuntime) {
  if (typeof getRuntime !== 'function') throw new TypeError('Invalid local account dependency.');
  return Object.freeze({
    create: (credentials) => runBrokerCommand(async () => {
      const valid = validateCredentials(credentials);
      return getRuntime().invoke('source.accounts.create', valid, { timeoutMs: 90000 });
    }),
    list: () => runBrokerCommand(() => getRuntime().invoke('source.accounts.list')),
    get: (connectionId) => runBrokerCommand(() => getRuntime().invoke(
      'source.accounts.get',
      { connection_id: validateConnectionId(connectionId) },
    )),
    reconnect: (connectionId, credentials) => runBrokerCommand(async () => {
      const valid = validateCredentials(credentials);
      return getRuntime().invoke(
        'source.accounts.reconnect',
        { connection_id: validateConnectionId(connectionId), ...valid },
        { timeoutMs: 90000 },
      );
    }),
    revoke: (connectionId) => runBrokerCommand(async () => {
      await getRuntime().invoke(
        'source.accounts.purge',
        { connection_id: validateConnectionId(connectionId) },
        { timeoutMs: 120000 },
      );
      return null;
    }),
  });
}

module.exports = { createLocalAccountBroker };

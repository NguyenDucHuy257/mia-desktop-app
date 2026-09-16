const { runBrokerCommand, validateConnectionId, validateCredentials } = require('./account-connection-broker.cjs');

function createLocalAccountBroker(getRuntime, authorizeUsername = async () => undefined) {
  if (typeof getRuntime !== 'function') throw new TypeError('Invalid local account dependency.');
  if (typeof authorizeUsername !== 'function') throw new TypeError('Invalid license authorization dependency.');
  return Object.freeze({
    create: (credentials) => runBrokerCommand(async () => {
      const valid = validateCredentials(credentials);
      // Refresh and enforce the exact licensed MST at the outer IPC boundary.
      // The runtime guard remains a second, independent line of defence.
      await authorizeUsername(valid.username);
      return getRuntime().invoke('source.accounts.create', valid, { timeoutMs: 90000 });
    }),
    list: () => runBrokerCommand(() => getRuntime().invoke('source.accounts.list')),
    get: (connectionId) => runBrokerCommand(() => getRuntime().invoke(
      'source.accounts.get',
      { connection_id: validateConnectionId(connectionId) },
    )),
    reconnect: (connectionId, credentials) => runBrokerCommand(async () => {
      const valid = validateCredentials(credentials);
      await authorizeUsername(valid.username);
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

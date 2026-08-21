const { runBrokerCommand, validateConnectionId, validateCredentials } = require('./account-connection-broker.cjs');

function createLocalAccountBroker(getRuntime, protector) {
  if (typeof getRuntime !== 'function' || !protector?.decrypt) throw new TypeError('Invalid local account dependencies.');

  async function sourceAccountsWithLegacyNames() {
    const runtime = getRuntime();
    let legacy = [];
    try {
      legacy = await runtime.invoke('accounts.list');
    } catch {
      legacy = [];
    }
    const legacyByUsername = new Map(legacy.map((item) => [item.username, item]));
    let source = await runtime.invoke('source.accounts.list');
    const sourceUsernames = new Set(source.map((item) => item.username));

    // One-way compatibility migration. Password decryption stays in Electron;
    // Python receives plaintext only for the source account-connection create
    // call and the source SessionCipher immediately persists its own envelope.
    for (const oldAccount of legacy) {
      if (sourceUsernames.has(oldAccount.username)) continue;
      try {
        const secret = await runtime.invoke('accounts.secret', { account_id: oldAccount.connection_id });
        const password = protector.decrypt(Buffer.from(secret.encrypted_password, 'base64'));
        const migrated = await runtime.invoke('source.accounts.create', {
          username: secret.username,
          password,
        }, { timeoutMs: 30000 });
        source.push(migrated);
        sourceUsernames.add(migrated.username);
      } catch {
        // Keep migration best-effort. The old row stays on disk until the user
        // explicitly purges it; source accounts remain the only rows returned.
      }
    }

    return source.map((item) => ({
      ...item,
      company_name: item.company_name ?? legacyByUsername.get(item.username)?.company_name ?? null,
    }));
  }

  return Object.freeze({
    create: (credentials) => runBrokerCommand(async () => {
      const valid = validateCredentials(credentials);
      return getRuntime().invoke('source.accounts.create', valid, { timeoutMs: 30000 });
    }),
    list: () => runBrokerCommand(sourceAccountsWithLegacyNames),
    get: (connectionId) => runBrokerCommand(() => getRuntime().invoke(
      'source.accounts.get',
      { connection_id: validateConnectionId(connectionId) },
    )),
    reconnect: (connectionId, credentials) => runBrokerCommand(async () => {
      const valid = validateCredentials(credentials);
      return getRuntime().invoke('source.accounts.reconnect', {
        connection_id: validateConnectionId(connectionId),
        ...valid,
      }, { timeoutMs: 30000 });
    }),
    revoke: (connectionId) => runBrokerCommand(async () => {
      await getRuntime().invoke(
        'source.accounts.purge',
        { connection_id: validateConnectionId(connectionId) },
        { timeoutMs: 300000 },
      );
      return null;
    }),
  });
}

module.exports = { createLocalAccountBroker };

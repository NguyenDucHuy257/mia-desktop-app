import type { AccountConnection } from '../../lib/api/contracts';
import type { MiaAccountConnectionsBridge } from '../../lib/runtime-bridge';
import type { AccountCredentials } from './account-form';

export interface AccountConnectionGateway {
  create(credentials: AccountCredentials): Promise<AccountConnection>;
  list(): Promise<AccountConnection[]>;
  get(connectionId: string): Promise<AccountConnection>;
  reconnect(connectionId: string, credentials: AccountCredentials): Promise<AccountConnection>;
  revoke(connectionId: string): Promise<void>;
}

export class AccountGatewayError extends Error {
  constructor(
    public readonly code: string,
    message: string,
    public readonly status?: number,
    public readonly requestId?: string,
  ) {
    super(message);
    this.name = 'AccountGatewayError';
  }
}

class RuntimeAccountConnectionGateway implements AccountConnectionGateway {
  constructor(private readonly bridge: MiaAccountConnectionsBridge) {}

  create(credentials: AccountCredentials) {
    return this.bridge.create(credentials);
  }

  list() { return this.bridge.list(); }

  get(connectionId: string) {
    return this.bridge.get(connectionId);
  }

  reconnect(connectionId: string, credentials: AccountCredentials) {
    return this.bridge.reconnect(connectionId, credentials);
  }

  revoke(connectionId: string) {
    return this.bridge.revoke(connectionId);
  }
}

export class InMemoryAccountConnectionGateway implements AccountConnectionGateway {
  private readonly connections = new Map<string, AccountConnection>();

  constructor(
    private readonly now: () => string = () => new Date().toISOString(),
    private readonly createId: () => string = () => `demo-${crypto.randomUUID()}`,
  ) {}

  async create(credentials: AccountCredentials) {
    const existing = [...this.connections.values()].find((item) => item.username === credentials.username);
    if (existing) return { ...existing, reused: true };

    const timestamp = this.now();
    const connection: AccountConnection = {
      connection_id: this.createId(),
      username: credentials.username,
      status: 'unchecked',
      token_generation: 1,
      created_at: timestamp,
      updated_at: timestamp,
      reused: false,
    };
    this.connections.set(connection.connection_id, connection);
    return { ...connection };
  }

  async list() { return [...this.connections.values()].map((item) => ({ ...item })); }

  async get(connectionId: string) {
    const connection = this.connections.get(connectionId);
    if (!connection) throw new AccountGatewayError('connection_not_found', 'Không tìm thấy kết nối.', 404);
    return { ...connection };
  }

  async reconnect(connectionId: string, credentials: AccountCredentials) {
    const connection = await this.get(connectionId);
    const updated: AccountConnection = {
      ...connection,
      username: credentials.username,
      status: 'unchecked',
      token_generation: connection.token_generation + 1,
      updated_at: this.now(),
      reused: false,
    };
    this.connections.set(connectionId, updated);
    return { ...updated };
  }

  async revoke(connectionId: string) {
    if (!this.connections.delete(connectionId)) {
      throw new AccountGatewayError('connection_not_found', 'Không tìm thấy kết nối.', 404);
    }
  }
}

class UnavailableAccountConnectionGateway implements AccountConnectionGateway {
  private reject(): never {
    throw new AccountGatewayError(
      'api_not_configured',
      'API desktop chưa được cấu hình. Hãy dùng xác thực thiết bị hoặc cấu hình staging trong Electron main process.',
    );
  }

  async create(): Promise<AccountConnection> { return this.reject(); }
  async list(): Promise<AccountConnection[]> { return this.reject(); }
  async get(): Promise<AccountConnection> { return this.reject(); }
  async reconnect(): Promise<AccountConnection> { return this.reject(); }
  async revoke(): Promise<void> { return this.reject(); }
}

function browserDemoWasRequested() {
  if (typeof window === 'undefined' || !/^https?:$/.test(window.location.protocol)) return false;
  return new URLSearchParams(window.location.search).get('demo') === '1';
}

export function createAccountConnectionGateway(): AccountConnectionGateway {
  if (typeof window !== 'undefined' && window.miaRuntime?.accountConnections) {
    return new RuntimeAccountConnectionGateway(window.miaRuntime.accountConnections);
  }
  if (browserDemoWasRequested()) return new InMemoryAccountConnectionGateway();
  return new UnavailableAccountConnectionGateway();
}

export async function createAccountConnectionsInBatches(
  gateway: AccountConnectionGateway,
  credentials: AccountCredentials[],
  batchSize = 3,
) {
  if (!Number.isInteger(batchSize) || batchSize < 1 || batchSize > 10) {
    throw new RangeError('batchSize must be an integer between 1 and 10');
  }

  const results: PromiseSettledResult<AccountConnection>[] = [];
  for (let index = 0; index < credentials.length; index += batchSize) {
    const batch = credentials.slice(index, index + batchSize);
    results.push(...await Promise.allSettled(batch.map((item) => gateway.create(item))));
  }
  return results;
}

export function accountErrorMessage(error: unknown) {
  const code = error instanceof AccountGatewayError
    ? error.code
    : typeof error === 'object' && error !== null && 'code' in error
      ? String(error.code)
      : 'unknown_error';

  const messages: Record<string, string> = {
    api_not_configured: 'Ứng dụng chưa được kết nối với API. Bản production cần hoàn tất xác thực thiết bị.',
    invalid_credentials: 'Mã số thuế hoặc mật khẩu không hợp lệ.',
    authentication_failed: 'Không thể đăng nhập Cổng HĐĐT. Vui lòng kiểm tra lại thông tin.',
    invalid_source_credentials: 'Tên đăng nhập hoặc mật khẩu không đúng.',
    source_account_locked: 'Tài khoản đã bị khóa vì nhập sai thông tin quá số lần quy định.',
    source_login_rejected: 'Cổng hóa đơn từ chối đăng nhập.',
    source_token_missing: 'Cổng hóa đơn không trả về phiên đăng nhập hợp lệ.',
    source_rate_limited: 'Cổng hóa đơn đang giới hạn truy cập. Vui lòng thử lại sau.',
    connection_not_found: 'Không tìm thấy kết nối tài khoản.',
    resource_not_found: 'Không tìm thấy kết nối tài khoản.',
    account_busy: 'Tài khoản đang được xử lý bởi một tác vụ khác.',
    capacity_exhausted: 'Hàng đợi xử lý đang đầy. Vui lòng thử lại sau.',
    network_error: 'Không thể kết nối máy chủ. Vui lòng kiểm tra mạng và thử lại.',
    api_timeout: 'Máy chủ phản hồi quá chậm. Vui lòng thử lại.',
  };

  if (code.startsWith('source_http_')) {
    return 'Dịch vụ Cổng HĐĐT đang tạm thời không khả dụng.';
  }
  return messages[code] ?? 'Không thể thêm tài khoản. Vui lòng thử lại.';
}

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
      'runtime_unavailable',
      'Bộ xử lý dữ liệu cục bộ chưa sẵn sàng.',
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
  _batchSize = 1,
) {
  // One local runtime verifies/creates one account at a time. Keep the settled
  // result shape used by the bulk-add UI without creating parallel portal logins.
  const results: PromiseSettledResult<AccountConnection>[] = [];
  for (const item of credentials) {
    try {
      results.push({ status: 'fulfilled', value: await gateway.create(item) });
    } catch (reason) {
      results.push({ status: 'rejected', reason });
    }
  }
  return results;
}

export function accountErrorMessage(error: unknown) {
  const code = error instanceof AccountGatewayError
    ? error.code
    : typeof error === 'object' && error !== null && 'code' in error
      ? String(error.code)
      : error instanceof Error
        ? /^\[([a-zA-Z0-9_]+)\]/.exec(error.message)?.[1] ?? 'unknown_error'
        : 'unknown_error';

  const messages: Record<string, string> = {
    source_account_data_invalid: 'Dữ liệu tài khoản hoặc phản hồi đăng nhập không đúng định dạng. Kiểm tra Nhật ký runtime để xác định bước đọc dữ liệu bị lỗi.',
    source_captcha_missing: 'Cổng hóa đơn không trả về CAPTCHA đầy đủ. Vui lòng thử lại sau.',
    source_captcha_model_failed: 'Bộ đọc CAPTCHA của phần mềm không chạy được. Kiểm tra Nhật ký và cài lại runtime từ bộ cài mới.',
    source_connection_failed: 'Không kết nối được Cổng hóa đơn. Kiểm tra mạng hoặc proxy.',
    source_tls_failed: 'Kết nối bảo mật tới Cổng hóa đơn thất bại. Kiểm tra ngày giờ máy và chứng chỉ mạng.',
    account_storage_denied: 'Không có quyền ghi dữ liệu tài khoản. Kiểm tra quyền truy cập thư mục lưu trữ.',
    account_runtime_dependency_missing: 'Runtime thiếu thư viện cần thiết để đăng nhập. Cài lại bằng bộ cài đầy đủ.',
    account_busy: 'Tài khoản đang được tác vụ khác sử dụng. Chờ tác vụ hoàn tất rồi thêm lại.',
    capacity_exhausted: 'Bộ xử lý đang hết lượt xử lý trống. Chờ tác vụ hiện tại hoàn tất.',
    runtime_not_running: 'Bộ xử lý đã dừng hoặc đang khởi động lại. Chờ runtime sẵn sàng rồi thử lại.',
    runtime_write_failed: 'Không gửi được yêu cầu tới bộ xử lý. Kiểm tra Nhật ký runtime.',
    storage_not_initialized: 'Kho dữ liệu tài khoản chưa khởi tạo xong. Chờ ứng dụng khởi động hoàn tất.',
    invalid_params: 'Dữ liệu đăng nhập hoặc dữ liệu trả về không đúng định dạng ứng dụng yêu cầu. Xem Nhật ký để xác định bước thất bại.',
    source_account_failed: 'Lỗi xử lý tài khoản trong runtime chưa được phân loại. Xem Nhật ký runtime tại thời điểm thêm tài khoản.',
    internal_error: 'Lỗi nội bộ khi thêm tài khoản. Cần Nhật ký runtime và Electron tại thời điểm xảy ra lỗi để xác định nguyên nhân.',
    LICENSE_REQUIRED: 'Giấy phép chưa được xác nhận. Vui lòng kiểm tra trạng thái kích hoạt ứng dụng.',
    runtime_timeout: 'Bộ xử lý đăng nhập phản hồi quá thời gian. Vui lòng thử lại.',
    source_timeout: 'Cổng hóa đơn phản hồi quá thời gian. Vui lòng thử lại.',
    source_captcha_failed: 'Không xác thực được CAPTCHA. Vui lòng thử lại.',
    runtime_unavailable: 'Bộ xử lý dữ liệu cục bộ chưa sẵn sàng. Vui lòng mở lại ứng dụng.',
    invalid_credentials: 'Mã số thuế hoặc mật khẩu không hợp lệ.',
    authentication_failed: 'Không thể đăng nhập Cổng HĐĐT. Vui lòng kiểm tra lại thông tin.',
    invalid_source_credentials: 'Tên đăng nhập hoặc mật khẩu không đúng.',
    source_account_locked: 'Tài khoản đã bị khóa vì nhập sai thông tin quá số lần quy định.',
    source_login_rejected: 'Cổng hóa đơn từ chối đăng nhập.',
    source_token_missing: 'Cổng hóa đơn không trả về phiên đăng nhập hợp lệ.',
    source_rate_limited: 'Cổng hóa đơn đang giới hạn truy cập. Vui lòng thử lại sau.',
    connection_not_found: 'Không tìm thấy kết nối tài khoản.',
    resource_not_found: 'Không tìm thấy kết nối tài khoản.',
  };

  if (code.startsWith('source_http_')) {
    return 'Dịch vụ Cổng HĐĐT đang tạm thời không khả dụng.';
  }
  return messages[code] ?? `Không thể hoàn tất thêm tài khoản. Mã chẩn đoán: ${/^[a-zA-Z0-9_]{1,80}$/.test(code) ? code : 'unknown_error'}. Xem Nhật ký tại thời điểm đăng nhập để xác định nguyên nhân.`;
}

export interface AccountCredentials {
  username: string;
  password: string;
}

export interface CredentialErrors {
  username?: string;
  password?: string;
}

export interface BulkAccountEntry extends AccountCredentials {
  lineNumber: number;
}

export interface BulkAccountError {
  lineNumber: number;
  message: string;
}

export interface BulkParseResult {
  entries: BulkAccountEntry[];
  errors: BulkAccountError[];
}

export const MAX_BULK_ACCOUNTS = 100;
const TAX_CODE_PATTERN = /^\d{10}(?:-\d{3})?$/;

export function normalizeTaxCode(value: string) {
  return value.trim();
}

export function validateCredentials(credentials: AccountCredentials): CredentialErrors {
  const username = normalizeTaxCode(credentials.username);
  const errors: CredentialErrors = {};

  if (!username) {
    errors.username = 'Vui lòng nhập mã số thuế.';
  } else if (!TAX_CODE_PATTERN.test(username)) {
    errors.username = 'Mã số thuế phải gồm 10 số hoặc dạng 10 số-3 số.';
  }

  if (!credentials.password) {
    errors.password = 'Vui lòng nhập mật khẩu.';
  } else if (credentials.password.length > 256) {
    errors.password = 'Mật khẩu không được dài quá 256 ký tự.';
  }

  return errors;
}

export function parseBulkAccounts(value: string): BulkParseResult {
  const entries: BulkAccountEntry[] = [];
  const errors: BulkAccountError[] = [];
  const seenTaxCodes = new Set<string>();

  value.split(/\r?\n/).forEach((rawLine, index) => {
    const lineNumber = index + 1;
    if (!rawLine.trim()) return;

    const separator = rawLine.indexOf('|');
    if (separator < 0) {
      errors.push({ lineNumber, message: 'Thiếu dấu phân cách |.' });
      return;
    }

    const username = normalizeTaxCode(rawLine.slice(0, separator));
    const password = rawLine.slice(separator + 1);
    const fieldErrors = validateCredentials({ username, password });
    const firstError = fieldErrors.username ?? fieldErrors.password;

    if (firstError) {
      errors.push({ lineNumber, message: firstError });
      return;
    }
    if (seenTaxCodes.has(username)) {
      errors.push({ lineNumber, message: 'Mã số thuế bị trùng trong danh sách.' });
      return;
    }
    if (entries.length >= MAX_BULK_ACCOUNTS) {
      errors.push({ lineNumber, message: `Chỉ được thêm tối đa ${MAX_BULK_ACCOUNTS} tài khoản mỗi lần.` });
      return;
    }

    seenTaxCodes.add(username);
    entries.push({ username, password, lineNumber });
  });

  if (entries.length === 0 && errors.length === 0) {
    errors.push({ lineNumber: 1, message: 'Vui lòng nhập ít nhất một tài khoản.' });
  }

  return { entries, errors };
}

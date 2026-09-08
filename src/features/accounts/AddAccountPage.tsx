import { FormEvent, useMemo, useState } from 'react';
import backIcon from '../../assets/figma/back.png';
import { NoticeDialog } from '../../components/NoticeDialog';
import { PasswordInput } from '../../components/PasswordInput';
import { diagnosticLog } from '../../lib/diagnostic-logger';
import {
  accountErrorMessage,
  createAccountConnectionsInBatches,
  createAccountConnectionGateway,
  type AccountConnectionGateway,
} from './account-gateway';
import {
  normalizeTaxCode,
  parseBulkAccounts,
  validateCredentials,
  type CredentialErrors,
} from './account-form';

type AccountTab = 'single' | 'bulk';

interface AddAccountPageProps {
  onBack(): void;
  onConnectionCreated?(connectionId: string): void;
  gateway?: AccountConnectionGateway;
}

interface Feedback {
  kind: 'success' | 'error';
  message: string;
}

export function AddAccountPage({ onBack, onConnectionCreated, gateway: gatewayOverride }: AddAccountPageProps) {
  const gateway = useMemo(
    () => gatewayOverride ?? createAccountConnectionGateway(),
    [gatewayOverride],
  );
  const [tab, setTab] = useState<AccountTab>('single');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [bulkValue, setBulkValue] = useState('');
  const [errors, setErrors] = useState<CredentialErrors>({});
  const [bulkError, setBulkError] = useState('');
  const [feedback, setFeedback] = useState<Feedback | null>(null);
  const [submitting, setSubmitting] = useState(false);

  function changeTab(nextTab: AccountTab) {
    if (submitting) return;
    setTab(nextTab);
    setErrors({});
    setBulkError('');
    setFeedback(null);
  }

  async function submitSingle(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const credentials = { username: normalizeTaxCode(username), password };
    const nextErrors = validateCredentials(credentials);
    setErrors(nextErrors);
    setFeedback(null);
    if (Object.keys(nextErrors).length > 0) return;

    setSubmitting(true);
    diagnosticLog('account_login_requested', { mode: 'single', username: credentials.username });
    try {
      const connection = await gateway.create(credentials);
      diagnosticLog('account_login_succeeded', { mode: 'single', connection_id: connection.connection_id, reused: connection.reused });
      onConnectionCreated?.(connection.connection_id);
      setUsername(connection.username);
      setPassword('');
      setFeedback({
        kind: 'success',
        message: connection.reused ? 'Tài khoản đã tồn tại và được dùng lại.' : 'Đã thêm tài khoản thành công.',
      });
    } catch (error) {
      diagnosticLog('account_login_failed', { mode: 'single', code: (error as { code?: string })?.code }, 'error');
      setFeedback({ kind: 'error', message: accountErrorMessage(error) });
    } finally {
      setSubmitting(false);
    }
  }

  async function submitBulk(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const parsed = parseBulkAccounts(bulkValue);
    setFeedback(null);
    if (parsed.errors.length > 0) {
      const first = parsed.errors[0]!;
      setBulkError(`Dòng ${first.lineNumber}: ${first.message}`);
      return;
    }

    setBulkError('');
    setSubmitting(true);
    diagnosticLog('account_login_requested', { mode: 'bulk', account_count: parsed.entries.length });
    const results = await createAccountConnectionsInBatches(
      gateway,
      parsed.entries.map(({ username: taxCode, password: portalPassword }) => ({
        username: taxCode,
        password: portalPassword,
      })),
    );
    const successful = results.filter((result) => result.status === 'fulfilled').length;
    const failed = results.length - successful;
    diagnosticLog(failed ? 'account_login_failed' : 'account_login_succeeded', {
      mode: 'bulk', account_count: results.length, successful, failed,
    }, failed ? 'error' : 'info');
    for (const result of results) {
      if (result.status === 'fulfilled') onConnectionCreated?.(result.value.connection_id);
    }

    if (failed === 0) {
      setBulkValue('');
      setFeedback({ kind: 'success', message: `Đã thêm ${successful} tài khoản thành công.` });
    } else {
      const firstFailure = results.find((result) => result.status === 'rejected');
      setFeedback({
        kind: 'error',
        message: `Đã thêm ${successful}/${results.length} tài khoản. ${
          firstFailure?.status === 'rejected' ? accountErrorMessage(firstFailure.reason) : ''
        }`,
      });
    }
    setSubmitting(false);
  }

  return (
    <section className="account-page" aria-label="Thêm tài khoản">
      <div className="account-card" data-tab={tab}>
        <header className="account-card-header">
          <button className="account-back" type="button" onClick={onBack}>
            <img src={backIcon} alt="" />
            <span>Quay lại Quản lý HĐĐT</span>
          </button>
          <h1>Thêm tài khoản</h1>
          <p>Quản lý và thêm mới tài khoản vào hệ thống</p>
        </header>

        <div className="account-tabs" role="tablist" aria-label="Cách thêm tài khoản">
          <button
            id="account-tab-single"
            className="account-tab"
            type="button"
            role="tab"
            aria-selected={tab === 'single'}
            aria-controls="account-panel-single"
            data-active={tab === 'single'}
            onClick={() => changeTab('single')}
          >
            Thêm đơn lẻ
          </button>
          <button
            id="account-tab-bulk"
            className="account-tab"
            type="button"
            role="tab"
            aria-selected={tab === 'bulk'}
            aria-controls="account-panel-bulk"
            data-active={tab === 'bulk'}
            onClick={() => changeTab('bulk')}
          >
            Thêm hàng loạt
          </button>
        </div>

        {tab === 'single' ? (
          <form
            id="account-panel-single"
            className="account-form account-form--single"
            role="tabpanel"
            aria-labelledby="account-tab-single"
            noValidate
            onSubmit={submitSingle}
          >
            <label className="account-field">
              <span>Mã số thuế (MST)</span>
              <input
                value={username}
                inputMode="numeric"
                autoComplete="username"
                aria-invalid={Boolean(errors.username)}
                aria-describedby={errors.username ? 'account-username-error' : undefined}
                placeholder="Nhập mã số thuế..."
                onChange={(event) => {
                  setUsername(event.target.value);
                  if (errors.username) setErrors((current) => ({ ...current, username: undefined }));
                }}
              />
              {errors.username ? <small id="account-username-error" className="field-error">{errors.username}</small> : null}
            </label>
            <div className="account-field">
              <label htmlFor="account-password">Mật khẩu: (mật khẩu vào trang hoadondientu.gdt.gov.vn)</label>
              <PasswordInput
                id="account-password"
                value={password}
                autoComplete="current-password"
                aria-invalid={Boolean(errors.password)}
                aria-describedby={errors.password ? 'account-password-error' : undefined}
                placeholder="Nhập mật khẩu..."
                onChange={(event) => {
                  setPassword(event.target.value);
                  if (errors.password) setErrors((current) => ({ ...current, password: undefined }));
                }}
              />
              {errors.password ? <small id="account-password-error" className="field-error">{errors.password}</small> : null}
            </div>
            <SubmitRow submitting={submitting} />
          </form>
        ) : (
          <form
            id="account-panel-bulk"
            className="account-form account-form--bulk"
            role="tabpanel"
            aria-labelledby="account-tab-bulk"
            noValidate
            onSubmit={submitBulk}
          >
            <label className="account-field account-field--bulk">
              <span>Nhập danh sách tài khoản (MST|PASSWORD)</span>
              <textarea
                value={bulkValue}
                spellCheck={false}
                aria-invalid={Boolean(bulkError)}
                aria-describedby={bulkError ? 'account-bulk-error' : undefined}
                placeholder="MST1|PASSWORD"
                onChange={(event) => {
                  setBulkValue(event.target.value);
                  if (bulkError) setBulkError('');
                }}
              />
              {bulkError ? <small id="account-bulk-error" className="field-error">{bulkError}</small> : null}
            </label>
            <SubmitRow submitting={submitting} />
          </form>
        )}
      </div>
      {feedback ? <NoticeDialog kind={feedback.kind} message={feedback.message} onClose={() => setFeedback(null)} /> : null}
    </section>
  );
}

function SubmitRow({ submitting }: { submitting: boolean }) {
  return (
    <div className="account-submit-row">
      <button className="account-submit" type="submit" disabled={submitting}>
        {submitting ? 'Đang thêm...' : 'Thêm ngay'}
      </button>
    </div>
  );
}

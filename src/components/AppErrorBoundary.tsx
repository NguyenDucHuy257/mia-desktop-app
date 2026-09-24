import { Component, type ErrorInfo, type PropsWithChildren, type ReactNode } from 'react';
import { ExportSupportLogButton } from './ExportSupportLogButton';
import { diagnosticLog } from '../lib/diagnostic-logger';

type State = { error: Error | null };

export class AppErrorBoundary extends Component<PropsWithChildren, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State { return { error }; }

  componentDidCatch(error: Error, info: ErrorInfo) {
    diagnosticLog('renderer_render_failed', {
      error_type: error.name, message: error.message, stack: error.stack,
      component_stack: info.componentStack,
    }, 'error');
  }

  render(): ReactNode {
    if (!this.state.error) return this.props.children;
    return <main className="license-gate-page"><section className="license-gate-card" role="alert">
      <h1>MIA gặp lỗi giao diện</h1>
      <p>Hãy tải log lỗi và gửi cho bộ phận kỹ thuật. Dữ liệu đăng nhập nhạy cảm không được đưa vào file log.</p>
      <ExportSupportLogButton />
      <button type="button" className="license-primary-button" onClick={() => window.location.reload()}>Khởi động lại giao diện</button>
    </section></main>;
  }
}

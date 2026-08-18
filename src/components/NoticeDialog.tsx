import { useEffect } from 'react';

interface NoticeDialogProps {
  kind: 'error' | 'notice';
  message: string;
  onClose(): void;
  actionLabel?: string;
  onAction?(): void;
}

export function NoticeDialog({ kind, message, onClose, actionLabel, onAction }: NoticeDialogProps) {
  useEffect(() => {
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', closeOnEscape);
    return () => document.removeEventListener('keydown', closeOnEscape);
  }, [onClose]);

  return (
    <div className="notice-backdrop" role="presentation" onMouseDown={(event) => {
      if (event.target === event.currentTarget) onClose();
    }}>
      <section className="notice-dialog" role="alertdialog" aria-modal="true" aria-label={kind === 'error' ? 'Thông báo lỗi' : 'Thông báo'}>
        <span className="notice-icon" data-kind={kind} aria-hidden="true">{kind === 'error' ? '×' : '!'}</span>
        <p>{message}</p>
        <div className="notice-actions">
          {actionLabel && onAction ? <button type="button" className="notice-action" onClick={onAction}>{actionLabel}</button> : null}
          <button type="button" className="notice-close" autoFocus onClick={onClose}>Đóng</button>
        </div>
      </section>
    </div>
  );
}

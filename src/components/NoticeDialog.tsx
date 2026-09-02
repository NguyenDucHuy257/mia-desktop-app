import { useEffect, useRef } from 'react';

export type NoticeKind = 'error' | 'warning' | 'info' | 'success' | 'notice';

interface NoticeDialogProps {
  kind: NoticeKind;
  message: string;
  path?: string;
  onClose(): void;
  actionLabel?: string;
  onAction?(): void;
}

export function NoticeDialog({ kind, message, path, onClose, actionLabel, onAction }: NoticeDialogProps) {
  const dialogRef = useRef<HTMLElement>(null);
  const semanticKind = kind === 'notice' ? 'warning' : kind;
  useEffect(() => {
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
      if (event.key === 'Tab') {
        const controls = [...(dialogRef.current?.querySelectorAll<HTMLElement>('button:not(:disabled), [href], input:not(:disabled)') ?? [])];
        if (!controls.length) return;
        const first = controls[0]!;
        const last = controls[controls.length - 1]!;
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
      }
    };
    document.addEventListener('keydown', closeOnEscape);
    return () => { document.removeEventListener('keydown', closeOnEscape); previousFocus?.focus(); };
  }, [onClose]);

  return (
    <div className="notice-backdrop" role="presentation" onMouseDown={(event) => {
      if (event.target === event.currentTarget) onClose();
    }}>
      <section ref={dialogRef} className="notice-dialog" role="alertdialog" aria-modal="true" aria-label={semanticKind === 'error' ? 'Thông báo lỗi' : semanticKind === 'success' ? 'Thông báo thành công' : semanticKind === 'info' ? 'Thông tin' : 'Thông báo cảnh báo'}>
        <span className="notice-icon" data-kind={semanticKind} aria-hidden="true">{semanticKind === 'error' ? '×' : semanticKind === 'success' ? '✓' : semanticKind === 'info' ? 'i' : '!'}</span>
        <p className="notice-message">{message}</p>
        {path ? <div className="notice-path-block"><strong>Đường dẫn:</strong><span className="popup-path">{path}</span></div> : null}
        <div className="notice-actions">
          {actionLabel && onAction ? <button type="button" className="notice-action" onClick={onAction}>{actionLabel}</button> : null}
          <button type="button" className="notice-close" autoFocus onClick={onClose}>Đóng</button>
        </div>
      </section>
    </div>
  );
}

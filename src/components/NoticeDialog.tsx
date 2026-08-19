import { useEffect, useRef } from 'react';

interface NoticeDialogProps {
  kind: 'error' | 'notice' | 'success';
  message: string;
  onClose(): void;
  actionLabel?: string;
  onAction?(): void;
}

export function NoticeDialog({ kind, message, onClose, actionLabel, onAction }: NoticeDialogProps) {
  const dialogRef = useRef<HTMLElement>(null);
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
      <section ref={dialogRef} className="notice-dialog" role="alertdialog" aria-modal="true" aria-label={kind === 'error' ? 'Thông báo lỗi' : kind === 'success' ? 'Thông báo thành công' : 'Thông báo'}>
        <span className="notice-icon" data-kind={kind} aria-hidden="true">{kind === 'error' ? '×' : kind === 'success' ? '✓' : '!'}</span>
        <p>{message}</p>
        <div className="notice-actions">
          {actionLabel && onAction ? <button type="button" className="notice-action" onClick={onAction}>{actionLabel}</button> : null}
          <button type="button" className="notice-close" autoFocus onClick={onClose}>Đóng</button>
        </div>
      </section>
    </div>
  );
}

export function StorageFolderPicker({ value, onChange, onBrowse, className = '', ariaLabel = 'Thư mục lưu trữ' }: {
  value: string;
  onChange(value: string): void;
  onBrowse(): void | Promise<void>;
  className?: string;
  ariaLabel?: string;
}) {
  return (
    <div className={`storage-folder-picker ${className}`.trim()}>
      <input
        aria-label={ariaLabel}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        title={value}
      />
      <button type="button" aria-label={`Chọn ${ariaLabel.toLocaleLowerCase('vi')}`} onClick={() => void onBrowse()}>
        <svg viewBox="0 0 24 20" aria-hidden="true" focusable="false">
          <path d="M2 4.5A2.5 2.5 0 0 1 4.5 2H9l2 2.5h8.5A2.5 2.5 0 0 1 22 7v8.5a2.5 2.5 0 0 1-2.5 2.5h-15A2.5 2.5 0 0 1 2 15.5v-11Z" />
          <path d="M2.5 8h19" />
        </svg>
        <span>Thư mục lưu trữ</span>
      </button>
    </div>
  );
}

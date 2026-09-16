import { InputHTMLAttributes, useRef, useState } from 'react';

type PasswordInputProps = Omit<InputHTMLAttributes<HTMLInputElement>, 'type'>;

export function PasswordInput(props: PasswordInputProps) {
  const [showPassword, setShowPassword] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  function toggleVisibility() {
    const input = inputRef.current;
    const selectionStart = input?.selectionStart ?? null;
    const selectionEnd = input?.selectionEnd ?? null;
    setShowPassword(current => !current);
    requestAnimationFrame(() => {
      if (!input) return;
      input.focus();
      if (selectionStart !== null && selectionEnd !== null) {
        input.setSelectionRange(selectionStart, selectionEnd);
      }
    });
  }

  return (
    <span className="password-input-control">
      <input {...props} ref={inputRef} type={showPassword ? 'text' : 'password'} />
      <button
        className="password-visibility-toggle"
        type="button"
        aria-label={showPassword ? 'Ẩn mật khẩu' : 'Hiện mật khẩu'}
        aria-pressed={showPassword}
        onMouseDown={(event) => event.preventDefault()}
        onClick={toggleVisibility}
      >
        {showPassword ? <EyeOffIcon /> : <EyeIcon />}
      </button>
    </span>
  );
}

function EyeIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M2.4 12s3.5-6 9.6-6 9.6 6 9.6 6-3.5 6-9.6 6-9.6-6-9.6-6Z" /><circle cx="12" cy="12" r="2.8" /></svg>;
}

function EyeOffIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 3l18 18M10.7 6.1A10.8 10.8 0 0 1 12 6c6.1 0 9.6 6 9.6 6a17.4 17.4 0 0 1-3 3.6M6.1 6.1C3.7 7.8 2.4 12 2.4 12s3.5 6 9.6 6c1.6 0 3-.4 4.2-1M9.9 9.9a3 3 0 0 0 4.2 4.2" /></svg>;
}

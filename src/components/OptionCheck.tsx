import checkIcon from '../assets/figma/check.svg';

export function OptionCheck({ checked, label, disabled, title, onChange }: {
  checked: boolean;
  label: string;
  disabled?: boolean;
  title?: string;
  onChange?(): void;
}) {
  return <label title={title}>
    <input className="option-input" type="checkbox" checked={checked} disabled={disabled} readOnly={!onChange} onChange={onChange} />
    <span className="option-box" data-checked={checked}>{checked ? <img src={checkIcon} alt="" /> : null}</span>
    {label}
  </label>;
}

export default function FilterSelect({ value, onChange, options, allLabel, ariaLabel }) {
  return (
    <select
      value={value}
      onChange={(event) => onChange(event.target.value)}
      aria-label={ariaLabel}
      className="rounded-md border border-gray-300 bg-white px-3 py-2 text-xs text-gray-700 outline-none focus:border-black focus:ring-2 focus:ring-black/10 sm:text-sm"
    >
      <option value="">{allLabel}</option>
      {options.map((opt) => (
        <option key={opt} value={opt}>
          {opt}
        </option>
      ))}
    </select>
  );
}

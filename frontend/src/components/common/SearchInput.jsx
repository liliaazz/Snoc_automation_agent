import { Search } from "lucide-react";

export default function SearchInput({ value, onChange, placeholder, ariaLabel }) {
  return (
    <div className="relative w-full sm:max-w-sm">
      <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
      <input
        type="search"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        aria-label={ariaLabel || placeholder}
        className="w-full rounded-md border border-gray-300 py-2 pl-9 pr-3 text-xs text-gray-800 outline-none focus:border-black focus:ring-2 focus:ring-black/10 sm:text-sm"
      />
    </div>
  );
}

import { badgeClasses } from "../../utils/statusConstants";

export default function SeverityBadge({ value }) {
  return (
    <span className={`inline-flex rounded-full px-2.5 py-1 text-[10px] font-semibold whitespace-nowrap ${badgeClasses(value)}`}>
      {value}
    </span>
  );
}

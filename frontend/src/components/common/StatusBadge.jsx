import { useTranslation } from "../../i18n/useTranslation";
import { badgeClasses } from "../../utils/statusConstants";

const STATUS_KEYS = {
  Received: "status.received",
  Processing: "status.processing",
  "Automatically Resolved": "status.resolved",
  Escalated: "status.escalated",
  Rejected: "status.rejected",
  Unauthorized: "status.unauthorized",
  Pending: "status.pending",
  "Not Yet Treated": "status.nyt",
  Treated: "status.treated",
  Canceled: "status.canceled",
};

export default function StatusBadge({ value }) {
  const { t } = useTranslation();
  const label = STATUS_KEYS[value] ? t(STATUS_KEYS[value]) : value || "—";
  return (
    <span className={`inline-flex rounded-full px-2.5 py-1 text-[10px] font-semibold whitespace-nowrap ${badgeClasses(value)}`}>
      {label}
    </span>
  );
}

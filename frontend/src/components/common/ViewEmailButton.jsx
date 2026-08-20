import { MailOpen } from "lucide-react";
import { useTranslation } from "../../i18n/useTranslation";

export default function ViewEmailButton({ onClick }) {
  const { t } = useTranslation();
  return (
    <button
      type="button"
      onClick={onClick}
      title={t("btn.viewEmail")}
      aria-label={t("btn.viewEmail")}
      className="inline-flex h-8 w-8 items-center justify-center rounded-md text-gray-600 hover:bg-[#eaf1ff] hover:text-[#2563eb]"
    >
      <MailOpen size={16} />
    </button>
  );
}

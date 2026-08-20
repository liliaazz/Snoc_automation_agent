import { FileSearch } from "lucide-react";
import { useTranslation } from "../../i18n/useTranslation";

export default function ViewDetailsButton({ onClick }) {
  const { t } = useTranslation();
  return (
    <button
      type="button"
      onClick={onClick}
      title={t("btn.viewDetails")}
      aria-label={t("btn.viewDetails")}
      className="inline-flex h-8 w-8 items-center justify-center rounded-md text-gray-600 hover:bg-[#fff3df] hover:text-[#ad6800]"
    >
      <FileSearch size={16} />
    </button>
  );
}

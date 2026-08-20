import { Loader2 } from "lucide-react";
import { useTranslation } from "../../i18n/useTranslation";

export default function LoadingState({ label }) {
  const { t } = useTranslation();
  return (
    <div className="flex min-h-[178px] flex-col items-center justify-center gap-2 rounded-2xl border border-dashed border-[#cdd2d8] bg-white p-6 text-center text-gray-500">
      <Loader2 size={22} className="animate-spin" />
      <span className="text-xs">{label || t("loading.default")}</span>
    </div>
  );
}

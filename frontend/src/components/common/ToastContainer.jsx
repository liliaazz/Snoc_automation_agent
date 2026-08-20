import { CheckCircle2, CircleAlert, Info, X } from "lucide-react";
import { useApp } from "../../context/AppContext";

const ICONS = { success: CheckCircle2, error: CircleAlert, info: Info };
const TONE_CLASSES = {
  success: "bg-[#eaf8f0] text-[#249c62] border-[#bfe8d1]",
  error: "bg-[#fdebec] text-[#cb3444] border-[#f4c3c8]",
  info: "bg-[#eaf1ff] text-[#2563eb] border-[#c7dbfc]",
};

export default function ToastContainer() {
  const { state, dismissToast } = useApp();

  if (!state.toasts.length) return null;

  return (
    <div className="fixed bottom-4 right-4 z-[200] flex w-[min(340px,92vw)] flex-col gap-2" role="status" aria-live="polite">
      {state.toasts.map((toast) => {
        const Icon = ICONS[toast.tone] || Info;
        return (
          <div
            key={toast.id}
            className={`flex items-start gap-2 rounded-xl border px-3 py-2.5 text-xs font-medium shadow-lg ${TONE_CLASSES[toast.tone] || TONE_CLASSES.info}`}
          >
            <Icon size={16} className="mt-0.5 shrink-0" />
            <span className="flex-1">{toast.message}</span>
            <button
              type="button"
              onClick={() => dismissToast(toast.id)}
              aria-label="Dismiss notification"
              className="shrink-0 opacity-70 hover:opacity-100"
            >
              <X size={14} />
            </button>
          </div>
        );
      })}
    </div>
  );
}

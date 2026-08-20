import { useTranslation } from "../../i18n/useTranslation";
import Modal from "./Modal";

export default function FormModal({ title, onClose, onSubmit, submitLabel, submitting, wide, children }) {
  const { t } = useTranslation();
  return (
    <Modal
      title={title}
      onClose={onClose}
      wide={wide}
      footer={
        <>
          <button
            type="button"
            onClick={onClose}
            className="rounded-md border border-gray-300 bg-white px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50"
          >
            {t("btn.cancel")}
          </button>
          <button
            type="button"
            onClick={onSubmit}
            disabled={submitting}
            className="rounded-md bg-black px-4 py-2 text-sm font-medium text-white hover:bg-[#ea8b00] disabled:cursor-progress disabled:opacity-60"
          >
            {submitLabel || t("btn.save")}
          </button>
        </>
      }
    >
      <form
        className="flex flex-col gap-3"
        onSubmit={(event) => {
          event.preventDefault();
          onSubmit();
        }}
      >
        {children}
      </form>
    </Modal>
  );
}

export function FormField({ label, error, children }) {
  return (
    <label className="flex flex-col gap-1 text-xs font-medium text-gray-600">
      {label}
      {children}
      {error ? <span className="text-[11px] font-normal text-[#cb3444]">{error}</span> : null}
    </label>
  );
}

export const inputClass =
  "rounded-md border border-gray-300 px-3 py-2 text-sm text-gray-900 outline-none focus:border-black focus:ring-2 focus:ring-black/10";

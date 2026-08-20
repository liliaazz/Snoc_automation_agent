import { useTranslation } from "../../i18n/useTranslation";
import Modal from "./Modal";

export default function ConfirmDialog({ title, message, confirmLabel, danger = false, onConfirm, onClose }) {
  const { t } = useTranslation();
  return (
    <Modal
      title={title}
      onClose={onClose}
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
            onClick={() => {
              onConfirm();
              onClose();
            }}
            className={`rounded-md px-4 py-2 text-sm font-medium text-white ${
              danger ? "bg-[#cb3444] hover:bg-[#b02c3a]" : "bg-black hover:bg-[#ea8b00]"
            }`}
          >
            {confirmLabel || t("btn.confirm")}
          </button>
        </>
      }
    >
      <p>{message}</p>
    </Modal>
  );
}

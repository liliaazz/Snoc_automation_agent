import { useMemo, useState } from "react";
import { useApp } from "../../context/AppContext";
import { useTranslation } from "../../i18n/useTranslation";
import { userService } from "../../services/userService";
import { configurationService } from "../../services/configurationService";
import { formatDateTime } from "../../utils/formatters";
import { validateUserForm } from "../../utils/validation";
import { WHITELIST_REGIONS } from "../../data/mockData";
import ConfirmDialog from "../common/ConfirmDialog";
import FormModal, { FormField, inputClass } from "../common/FormModal";
import SearchInput from "../common/SearchInput";
import FilterSelect from "../common/FilterSelect";
import StatusBadge from "../common/StatusBadge";
import PageTabs from "../common/PageTabs";

const EMPTY_FORM = { username: "", fullname: "", email: "", role: "user", tempPassword: "" };
const ACCOUNT_TABS = [
  { id: "users", label: "Users" },
  { id: "whitelist", label: "Whitelist shortcut" },
];

export default function AccountManagement() {
  const { state, dispatch, pushToast } = useApp();
  const { t, lang } = useTranslation();
  const [modal, setModal] = useState(null); // { mode: "add" | "edit", user }
  const [form, setForm] = useState(EMPTY_FORM);
  const [errors, setErrors] = useState({});
  const [deleteTarget, setDeleteTarget] = useState(null);
  const [saving, setSaving] = useState(false);
  const [newEmail, setNewEmail] = useState("");
  const [newEmailRegion, setNewEmailRegion] = useState("All");

  const [query, setQuery] = useState("");
  const [roleFilter, setRoleFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");

  const visibleUsers = useMemo(() => {
    const q = query.trim().toLowerCase();
    return state.users.filter((user) => {
      const matchesQuery =
        !q || [user.username, user.email].filter(Boolean).join(" ").toLowerCase().includes(q);
      const matchesRole = !roleFilter || user.role === roleFilter;
      const matchesStatus = !statusFilter || user.status === statusFilter;
      return matchesQuery && matchesRole && matchesStatus;
    });
  }, [state.users, query, roleFilter, statusFilter]);

  function openAdd() {
    setForm(EMPTY_FORM);
    setErrors({});
    setModal({ mode: "add" });
  }

  function openEdit(user) {
    setForm({ username: user.username, fullname: user.fullname, email: user.email, role: user.role, tempPassword: "" });
    setErrors({});
    setModal({ mode: "edit", user });
  }

  async function submitForm() {
    const { tempPassword, ...userFields } = form;
    const validation = validateUserForm({ ...userFields, users: state.users, excludeId: modal?.user?.id });
    if (modal?.mode === "add" && !tempPassword.trim()) {
      validation.tempPassword = "A temporary password is required by the backend.";
    } else if (tempPassword && tempPassword.length < 8) {
      validation.tempPassword = "Password must contain at least 8 characters.";
    }
    setErrors(validation);
    if (Object.keys(validation).length) {
      pushToast(t("toast.validationError"), "error");
      return;
    }
    setSaving(true);
    try {
      if (modal.mode === "add") {
        const user = await userService.addUser({ ...userFields, tempPassword });
        dispatch({ type: "ADD_USER", user: { ...user, fullname: userFields.fullname, email: userFields.email } });
        pushToast(t("toast.userAdded"), "success");
      } else {
        const updated = { ...modal.user, ...userFields, tempPassword };
        await userService.editUser(modal.user.username, updated);
        dispatch({ type: "UPDATE_USER", user: { ...updated, id: modal.user.id } });
        pushToast(t("toast.userUpdated"), "success");
      }
      setForm(EMPTY_FORM);
      setModal(null);
    } catch (error) {
      pushToast(error?.message || t("toast.validationError"), "error");
    } finally {
      setSaving(false);
    }
  }

  async function toggleStatus(user) {
    const nextStatus = user.status === "Active" ? "Inactive" : "Active";
    await userService.setUserStatus(user.username);
    dispatch({ type: "SET_USER_STATUS", id: user.id, status: nextStatus });
    pushToast(t("toast.saved"), "success");
  }

  async function confirmDelete() {
    if (!deleteTarget) return;
    await userService.deleteUser(deleteTarget.username);
    dispatch({ type: "DELETE_USER", id: deleteTarget.id });
    pushToast(t("toast.userDeleted"), "success");
  }

  async function addWhitelistEmail() {
    try {
      const entry = await configurationService.addWhitelistEmail(
        newEmail,
        state.whitelist,
        state.currentUser.name,
        null,
        newEmailRegion === "All" ? "Unknown" : newEmailRegion,
      );
      dispatch({ type: "ADD_WHITELIST", entry });
      setNewEmail("");
      pushToast(t("toast.whitelistAdded"), "success");
    } catch (err) {
      pushToast(err.message === "duplicate-email" ? t("toast.whitelistDuplicate") : t("toast.whitelistInvalid"), "error");
    }
  }

  async function removeWhitelistEmail(email) {
    await configurationService.removeWhitelistEmail(email);
    dispatch({ type: "REMOVE_WHITELIST", email });
    pushToast(t("toast.whitelistRemoved"), "success");
  }

  return (
    <>
      <PageTabs tabs={ACCOUNT_TABS}>
        {(active) => (
          <>
            {active === "users" ? (
              <div className="requests-card dashboard-card" style={{ marginTop: 0 }}>
                <div className="table-title-row flex-col items-stretch gap-3 sm:flex-row sm:items-center">
                  <div>
                    <h2>{t("accounts.users")}</h2>
                    <p>{state.users.length} users</p>
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    <SearchInput value={query} onChange={setQuery} placeholder="Search username or email…" />
                    <FilterSelect value={roleFilter} onChange={setRoleFilter} options={["admin", "user"]} allLabel="Role: All" ariaLabel="Filter by role" />
                    <FilterSelect
                      value={statusFilter}
                      onChange={setStatusFilter}
                      options={["Active", "Inactive"]}
                      allLabel="Status: All"
                      ariaLabel="Filter by status"
                    />
                    <button type="button" onClick={openAdd} className="primary-button">
                      {t("btn.addUser")}
                    </button>
                  </div>
                </div>
                <div className="table-scroll">
                  <table>
                    <thead>
                      <tr>
                        <th>{t("field.username")}</th>
                        <th>{t("field.fullname")}</th>
                        <th>{t("field.email")}</th>
                        <th>{t("field.role")}</th>
                        <th>{t("field.status")}</th>
                        <th>{t("field.lastLogin")}</th>
                        <th>{t("table.action")}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {visibleUsers.map((user) => (
                        <tr key={user.id}>
                          <td>{user.username}</td>
                          <td>{user.fullname}</td>
                          <td>{user.email}</td>
                          <td>{t(user.role === "admin" ? "role.admin" : "role.user")}</td>
                          <td>
                            <StatusBadge value={user.status} />
                          </td>
                          <td>{user.lastLogin === "—" ? "—" : formatDateTime(user.lastLogin, lang)}</td>
                          <td>
                            <div className="flex items-center gap-1">
                              <button
                                type="button"
                                onClick={() => openEdit(user)}
                                className="rounded-md px-2 py-1 text-xs font-medium text-gray-600 hover:bg-gray-100"
                              >
                                {t("btn.edit")}
                              </button>
                              <button
                                type="button"
                                onClick={() => toggleStatus(user)}
                                title={user.status === "Active" ? t("btn.deactivate") : t("btn.activate")}
                                aria-label={user.status === "Active" ? t("btn.deactivate") : t("btn.activate")}
                                className={`rounded-full px-3 py-1 text-[11px] font-semibold transition ${
                                  user.status === "Active"
                                    ? "bg-[#eaf8f0] text-[#249c62] hover:bg-[#fdebec] hover:text-[#cb3444]"
                                    : "bg-[#fdebec] text-[#cb3444] hover:bg-[#eaf8f0] hover:text-[#249c62]"
                                }`}
                              >
                                {user.status === "Active" ? t("status.active") : t("status.inactive")}
                              </button>
                              <button
                                type="button"
                                onClick={() => setDeleteTarget(user)}
                                className="rounded-md px-2 py-1 text-xs font-medium text-[#cb3444] hover:bg-[#fdebec]"
                              >
                                {t("btn.delete")}
                              </button>
                            </div>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            ) : null}

            {active === "whitelist" ? (
              <div className="requests-card dashboard-card" style={{ marginTop: 0 }}>
                <div className="table-title-row flex-col items-stretch gap-3 sm:flex-row sm:items-center">
                  <div>
                    <h2>{t("whitelist.entries")}</h2>
                    <p>{state.whitelist.length} entries</p>
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    <SearchInput value={newEmail} onChange={setNewEmail} placeholder={t("whitelist.placeholder")} ariaLabel={t("btn.addEmail")} />
                    <FilterSelect
                      value={newEmailRegion}
                      onChange={setNewEmailRegion}
                      options={WHITELIST_REGIONS}
                      allLabel="Region"
                      ariaLabel="Whitelist region"
                    />
                    <button type="button" onClick={addWhitelistEmail} className="primary-button">
                      {t("btn.addEmail")}
                    </button>
                  </div>
                </div>
                <div className="table-scroll">
                  <table>
                    <thead>
                      <tr>
                        <th>{t("field.email")}</th>
                        <th>Region</th>
                        <th>{t("whitelist.addedBy")}</th>
                        <th>{t("whitelist.addedAt")}</th>
                        <th>{t("table.action")}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {state.whitelist.map((entry) => (
                        <tr key={entry.email}>
                          <td>{entry.email}</td>
                          <td>{entry.region || "All"}</td>
                          <td>{entry.addedBy}</td>
                          <td>{formatDateTime(entry.addedAt, lang)}</td>
                          <td>
                            <button
                              type="button"
                              onClick={() => removeWhitelistEmail(entry.email)}
                              className="rounded-md px-2 py-1 text-xs font-medium text-[#cb3444] hover:bg-[#fdebec]"
                            >
                              {t("btn.delete")}
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            ) : null}
          </>
        )}
      </PageTabs>

      {modal ? (
        <FormModal
          title={modal.mode === "add" ? t("modal.addUser") : t("modal.editUser")}
          onClose={() => setModal(null)}
          onSubmit={submitForm}
          submitting={saving}
        >
          <FormField label={t("field.username")} error={errors.username}>
            <input
              value={form.username}
              onChange={(event) => setForm((prev) => ({ ...prev, username: event.target.value }))}
              className={inputClass}
            />
          </FormField>
          <FormField label={t("field.fullname")} error={errors.fullname}>
            <input
              value={form.fullname}
              onChange={(event) => setForm((prev) => ({ ...prev, fullname: event.target.value }))}
              className={inputClass}
            />
          </FormField>
          <FormField label={t("field.email")} error={errors.email}>
            <input
              value={form.email}
              onChange={(event) => setForm((prev) => ({ ...prev, email: event.target.value }))}
              className={inputClass}
            />
          </FormField>
          <FormField label={t("field.role")}>
            <select
              value={form.role}
              onChange={(event) => setForm((prev) => ({ ...prev, role: event.target.value }))}
              className={inputClass}
            >
              <option value="user">{t("role.user")}</option>
              <option value="admin">{t("role.admin")}</option>
            </select>
          </FormField>
          <FormField label={modal.mode === "add" ? t("field.tempPassword") : "New password (optional)"} error={errors.tempPassword}>
              <input
                type="password"
                value={form.tempPassword}
                onChange={(event) => setForm((prev) => ({ ...prev, tempPassword: event.target.value }))}
                className={inputClass}
                autoComplete="new-password"
              />
              <p className="mt-1 text-[10px] text-gray-400">
                {t("field.tempPasswordNote")}
              </p>
          </FormField>
        </FormModal>
      ) : null}

      {deleteTarget ? (
        <ConfirmDialog
          title={t("modal.deleteUser")}
          message={t("confirm.deleteUser")}
          confirmLabel={t("btn.deleteUser")}
          danger
          onConfirm={confirmDelete}
          onClose={() => setDeleteTarget(null)}
        />
      ) : null}
    </>
  );
}

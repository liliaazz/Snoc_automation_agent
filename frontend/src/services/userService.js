import backendApi from "./backendApi.js";

function normalizeAccount(account) {
  return {
    id: account.id || account.username,
    username: account.username,
    fullname: account.fullname || account.username,
    email: account.email || "",
    role: account.role === "normal" ? "user" : account.role || "user",
    status: account.active === false ? "Inactive" : "Active",
    lastLogin: account.lastLogin || "—",
    managedByEnv: Boolean(account.managedByEnv),
  };
}

export const userService = {
  async addUser(payload) {
    const response = await backendApi.createAccount({
      username: payload.username,
      password: payload.tempPassword,
      full_name: payload.fullname,
      email: payload.email,
      role: payload.role,
    });
    return normalizeAccount(response.account);
  },

  async editUser(originalUsername, payload) {
    const response = await backendApi.updateAccount(originalUsername, {
      username: payload.username || null,
      password: payload.tempPassword || null,
      full_name: payload.fullname,
      email: payload.email,
      role: payload.role,
    });
    return normalizeAccount(response.account);
  },

  async deleteUser(username) {
    await backendApi.deleteAccount(username);
    return username;
  },

  async setUserStatus(username) {
    const response = await backendApi.toggleAccount(username);
    return { username, active: response?.active };
  },
};

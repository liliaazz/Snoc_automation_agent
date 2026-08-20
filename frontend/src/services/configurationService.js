import backendApi from "./backendApi.js";
import { isDuplicateEmail, isValidEmail } from "../utils/validation";

export const configurationService = {
  async addWhitelistEmail(email, whitelist, addedBy, expiresAt = null, region = "Unknown") {
    const trimmed = String(email || "").trim();
    if (!isValidEmail(trimmed)) throw new Error("invalid-email");
    if (isDuplicateEmail(trimmed, whitelist)) throw new Error("duplicate-email");
    await backendApi.addWhitelist(trimmed, region);
    return { email: trimmed, region, addedBy, addedAt: new Date().toISOString(), expiresAt };
  },

  async removeWhitelistEmail(email) {
    await backendApi.removeWhitelist(email);
    return email;
  },

  async importWhitelistCsv(rows, whitelist, addedBy, region = "Unknown") {
    const existing = new Set(whitelist.map((entry) => entry.email.toLowerCase()));
    const added = [];
    for (const row of rows) {
      const email = String(row || "").trim();
      if (!isValidEmail(email) || existing.has(email.toLowerCase())) continue;
      await backendApi.addWhitelist(email, region);
      existing.add(email.toLowerCase());
      added.push({ email, region, addedBy, addedAt: new Date().toISOString(), expiresAt: null });
    }
    return added;
  },
};

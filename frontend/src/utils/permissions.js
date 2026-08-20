export const ROLE_ADMIN = "admin";
export const ROLE_USER = "user";

export function isAdmin(role) {
  return role === ROLE_ADMIN;
}

// Pages blocked for non-admins, even via direct hash navigation.
export const ADMIN_ONLY_PAGES = new Set(["accounts"]);

export function canAccessPage(pageId, role) {
  if (ADMIN_ONLY_PAGES.has(pageId)) return isAdmin(role);
  return true;
}

export function canEditRuleThreshold(role) {
  return isAdmin(role);
}

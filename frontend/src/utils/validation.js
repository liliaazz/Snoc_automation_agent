const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export function isValidEmail(value) {
  return EMAIL_RE.test(String(value || "").trim());
}

export function isDuplicateEmail(email, list, excludeId) {
  const normalized = String(email || "").trim().toLowerCase();
  return list.some(
    (item) =>
      (item.id === undefined || item.id !== excludeId) &&
      String(item.email || item).trim().toLowerCase() === normalized,
  );
}

export function isDuplicateUsername(username, users, excludeId) {
  const normalized = String(username || "").trim().toLowerCase();
  return users.some((u) => u.id !== excludeId && u.username.trim().toLowerCase() === normalized);
}

export function validateUserForm({ username, fullname, email, users, excludeId }) {
  const errors = {};
  if (!username || !username.trim()) errors.username = "Username is required.";
  else if (isDuplicateUsername(username, users, excludeId)) errors.username = "That username is already taken.";
  if (!fullname || !fullname.trim()) errors.fullname = "Full name is required.";
  if (!email || !email.trim()) errors.email = "Email is required.";
  else if (!isValidEmail(email)) errors.email = "Enter a valid email address.";
  else if (isDuplicateEmail(email, users, excludeId)) errors.email = "That email is already in use.";
  return errors;
}

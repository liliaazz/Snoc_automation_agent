// Shared helper for the mock service layer. Every mock service function is
// async and returns a Promise so it can be swapped for a real fetch() call
// later without touching any component code.
export const delay = (ms = 300) => new Promise((resolve) => setTimeout(resolve, ms));

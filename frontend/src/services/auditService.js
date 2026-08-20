import { delay } from "./mockApi";

export const auditService = {
  async getAuditLogs(auditLogs) {
    await delay(250);
    return auditLogs;
  },
};

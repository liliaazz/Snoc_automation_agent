import backendApi from "./backendApi.js";

export const emailService = {
  async processInbox() {
    const payload = await backendApi.processInbox();
    return {
      processed: Number(payload?.processed || 0),
      escalated: Number(payload?.escalated || 0),
      resolved: Number(payload?.resolved || 0),
    };
  },
};

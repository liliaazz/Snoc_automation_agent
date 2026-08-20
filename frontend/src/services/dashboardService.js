import { delay } from "./mockApi";

export const dashboardService = {
  // Thin wrapper used by the header Refresh button. The live summary/trends
  // data itself continues to come from useDashboard's existing API calls;
  // this just gives Refresh its own async affordance (loading + toast).
  async refreshDashboard(refreshFn) {
    await delay(150);
    refreshFn();
    return true;
  },
};

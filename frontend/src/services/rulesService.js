import { delay } from "./mockApi";

export const rulesService = {
  async getRules(rules) {
    await delay(200);
    return rules;
  },

  async toggleRule(id) {
    await delay(200);
    return { id };
  },

  async updateRuleThreshold(id, threshold) {
    await delay(250);
    return { id, threshold };
  },
};

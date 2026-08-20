import backendApi from "./backendApi.js";

export const escalationService = {
  async updateEscalationStatus(escalation, status, note, updatedBy) {
    if (!escalation?.id || !status) throw new Error("Missing escalation id or status.");

    let backendUpdated = false;
    const publicReference = escalation.publicReference;
    if (
      publicReference?.startsWith("SNOC-REQ-") &&
      escalation.formalEscalation &&
      (status === "Treated" || status === "Canceled")
    ) {
      await backendApi.resolveEscalation(
        publicReference,
        status === "Treated" ? "approve" : "reject",
        note || "",
      );
      backendUpdated = true;
    }

    return {
      id: escalation.id,
      status,
      note: note || "",
      updatedBy,
      updatedAt: new Date().toISOString(),
      backendUpdated,
    };
  },
};

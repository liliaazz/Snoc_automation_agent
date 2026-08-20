"""Deterministic, user-friendly French email templates."""

from __future__ import annotations

from dataclasses import dataclass

from snoc_agent.domain.enums import OperationAction

ACTION_LABELS = {
    OperationAction.VPN_ACCESS.value: "accès VPN/SNOC",
    OperationAction.OTP_NUMBER_CHANGE.value: "changement du numéro OTP",
    OperationAction.ACCOUNT_UNBLOCK.value: "déblocage du compte",
    OperationAction.PASSWORD_RESET.value: "réinitialisation du mot de passe",
    OperationAction.UNKNOWN.value: "opération non déterminée",
}
FIELD_LABELS = {
    "pdv_code": "code PDV (8 chiffres)",
    "phone": "numéro de téléphone",
    "new_phone": "nouveau numéro OTP",
}


@dataclass(frozen=True, slots=True)
class OperationMailView:
    sequence_number: int
    action: str
    pdv_code: str | None
    missing_fields: tuple[str, ...] = ()
    status_label: str = ""


def _operation_description(operation: OperationMailView) -> str:
    label = ACTION_LABELS.get(operation.action, "traitement de la demande")
    description = label[:1].upper() + label[1:]
    pdv_code = (operation.pdv_code or "").strip()
    if len(pdv_code) >= 4:
        description += f" pour le PDV se terminant par {pdv_code[-4:]}"
    elif pdv_code:
        description += " pour le PDV concerné"
    return description


def clarification_email(reference: str, operations: list[OperationMailView]) -> tuple[str, str]:
    subject = "Informations nécessaires pour votre demande"
    lines = [
        "Bonjour,",
        "",
        "Merci pour votre message.",
        "Pour poursuivre le traitement de votre demande, nous avons besoin des "
        "informations suivantes :",
        "",
    ]
    for operation in operations:
        lines.append(f"- {_operation_description(operation)}")
        labels = ", ".join(FIELD_LABELS.get(field, field) for field in operation.missing_fields)
        lines.append(f"  Information nécessaire : {labels}")
        lines.append("")
    lines.extend(
        [
            "Vous pouvez simplement répondre à cet email en indiquant les informations demandées.",
            "",
            f"Référence de suivi : {reference}",
            "",
            "Cordialement,",
            "Support SNOC",
        ]
    )
    return subject, "\n".join(lines)


def completion_email(reference: str, operations: list[OperationMailView]) -> tuple[str, str]:
    subject = "Mise à jour de votre demande"
    lines = [
        "Bonjour,",
        "",
        "Voici la mise à jour concernant votre demande :",
        "",
    ]
    for operation in operations:
        lines.append(f"- {_operation_description(operation)} : {operation.status_label}.")
    lines.extend(
        [
            "",
            f"Référence de suivi : {reference}",
            "",
            "Cordialement,",
            "Support SNOC",
        ]
    )
    return subject, "\n".join(lines)


def pending_execution_email(
    reference: str,
    operations: list[OperationMailView],
    *,
    grace_seconds: int,
) -> tuple[str, str]:
    """Acknowledge durable validation without claiming that execution completed."""

    subject = "Votre demande a été prise en compte"
    delay_unit = "seconde" if grace_seconds == 1 else "secondes"
    lines = [
        "Bonjour,",
        "",
        "Votre demande a bien été reçue et validée.",
        f"Son traitement automatique commencera dans environ {grace_seconds} {delay_unit}.",
        "",
    ]
    for operation in operations:
        lines.append(f"- {_operation_description(operation)}")
    lines.extend(
        [
            "",
            "Si vous souhaitez corriger ou annuler cette demande, répondez directement à ce message.",
            "",
            f"Référence de suivi : {reference}",
            "",
            "Cordialement,",
            "Support SNOC",
        ]
    )
    return subject, "\n".join(lines)

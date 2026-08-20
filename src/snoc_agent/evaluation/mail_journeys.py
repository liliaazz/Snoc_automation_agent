"""Production-style synthetic mailbox journeys and their RFC message builder."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import format_datetime


@dataclass(frozen=True, slots=True)
class MailJourneyScenario:
    name: str
    subject: str
    body: str
    expected: str
    reply_body: str | None = None
    automated: bool = False


MAIL_JOURNEY_SCENARIOS = (
    MailJourneyScenario(
        name="incomplete_otp_thread",
        subject="Codes OTP absents sur le téléphone du magasin",
        body=(
            "Bonjour,\n\nPouvez-vous changer le numéro qui reçoit les codes pour le "
            "point de vente 22000001 ? Je n'ai pas encore le nouveau numéro sous la main.\n\n"
            "Merci."
        ),
        expected="clarification reply, same-thread answer, then completed or safely escalated",
        reply_body=(
            "Bonjour,\n\nJe confirme explicitement que, pour l'opération OP-01 de changement "
            "du numéro OTP du point de vente 22000001, le champ « nouveau numéro OTP » est "
            "0770000001. Ce numéro doit recevoir les codes OTP.\n\nMerci."
        ),
    ),
    MailJourneyScenario(
        name="complete_unblock",
        subject="Compte bloqué au magasin",
        body=(
            "Bonjour,\n\nLe compte du point de vente 12000001 est bloqué depuis ce matin. "
            "Pouvez-vous le débloquer s'il vous plaît ?\n\nMerci."
        ),
        expected="terminal reply and one dry-run account-unblock execution",
    ),
    MailJourneyScenario(
        name="complete_vpn",
        subject="Accès VPN pour une nouvelle collègue",
        body=(
            "Bonjour,\n\nMerci de créer l'accès VPN du point de vente 23000001 pour notre "
            "nouvelle collègue. Son numéro est le 0661000001.\n\nCordialement."
        ),
        expected="terminal reply and one dry-run VPN execution",
    ),
    MailJourneyScenario(
        name="quoted_closed_history",
        subject="Mot de passe à réinitialiser",
        body=(
            "Bonjour,\n\nPouvez-vous réinitialiser le mot de passe du point de vente "
            "44000001 ? C'est bien la demande actuelle.\n\n"
            "Le lun. 20 juil. 2026 à 09:12, Support a écrit :\n"
            "> Ancienne demande clôturée pour le point de vente 55000002."
        ),
        expected="use the current PDV and never the quoted historical PDV",
    ),
    MailJourneyScenario(
        name="multi_operation_ambiguous",
        subject="Deux problèmes d'accès au magasin",
        body=(
            "Bonjour,\n\nIl faut débloquer un compte et changer le numéro OTP. Les points de "
            "vente concernés sont 33000001 et 33000002, mais je ne sais plus quel numéro "
            "correspond à quelle demande. Le nouveau téléphone serait le 0551000001.\n\nMerci."
        ),
        expected="separate the possible operations but do not execute ambiguous attribution",
    ),
    MailJourneyScenario(
        name="automated_out_of_office",
        subject="Réponse automatique : absence du bureau",
        body="Je suis absent du bureau et répondrai à mon retour.",
        expected="ignored before model inference",
        automated=True,
    ),
)


def build_journey_message(
    *,
    sender: str,
    recipient: str,
    subject: str,
    body: str,
    message_id: str,
    test_run_id: str,
    test_case: str,
    automated: bool = False,
    in_reply_to: str | None = None,
    references: tuple[str, ...] = (),
) -> bytes:
    """Build a natural-looking message with test identity only in hidden headers."""

    message = EmailMessage()
    message["From"] = sender
    message["To"] = recipient
    message["Subject"] = subject
    message["Date"] = format_datetime(datetime.now(UTC))
    message["Message-ID"] = message_id
    message["X-SNOC-Test-Run"] = "docker-e2e"
    message["X-SNOC-Test-Run-ID"] = test_run_id
    message["X-SNOC-Test-Case"] = test_case
    if automated:
        message["Auto-Submitted"] = "auto-replied"
    if in_reply_to:
        message["In-Reply-To"] = in_reply_to
    if references:
        message["References"] = " ".join(references)
    message.set_content(body)
    return message.as_bytes()

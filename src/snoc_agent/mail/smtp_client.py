"""Synchronous SMTP transport with bounded connection behavior."""

from __future__ import annotations

import smtplib
import ssl

from snoc_agent.mail.interfaces import OutboundEnvelope, SendResult


class RealSMTPTransport:
    def __init__(
        self,
        *,
        host: str,
        port: int,
        username: str = "",
        password: str = "",
        use_ssl: bool = True,
        starttls: bool = False,
        timeout: float = 30.0,
    ) -> None:
        if use_ssl and starttls:
            raise ValueError("implicit SSL and STARTTLS are mutually exclusive")
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.use_ssl = use_ssl
        self.starttls = starttls
        self.timeout = timeout

    def send(self, envelope: OutboundEnvelope) -> SendResult:
        cls = smtplib.SMTP_SSL if self.use_ssl else smtplib.SMTP
        try:
            with cls(self.host, self.port, timeout=self.timeout) as client:
                if self.starttls:
                    client.starttls(context=ssl.create_default_context())
                if self.username:
                    client.login(self.username, self.password)
                refused = client.sendmail(
                    envelope.sender, list(envelope.recipients), envelope.raw_message
                )
                if refused:
                    return SendResult(False, detail=f"recipients refused: {sorted(refused)}")
                return SendResult(True, detail="accepted by SMTP")
        except (smtplib.SMTPServerDisconnected, TimeoutError, OSError) as exc:
            return SendResult(False, transient_failure=True, detail=str(exc))
        except smtplib.SMTPException as exc:
            return SendResult(False, transient_failure=False, detail=str(exc))

"""Synchronous UID-based IMAP implementation using BODY.PEEK."""

from __future__ import annotations

import imaplib
import logging
import re
import shlex
from contextlib import suppress
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

from snoc_agent.mail.interfaces import MailboxMessage

UIDVALIDITY_RE = re.compile(
    rb"UIDVALIDITY\s+(\d+)",
    re.IGNORECASE,
)

FLAGS_RE = re.compile(
    rb"FLAGS\s*\(([^)]*)\)",
    re.IGNORECASE,
)

INTERNALDATE_RE = re.compile(
    rb'INTERNALDATE\s+"([^"]+)"',
    re.IGNORECASE,
)

GMAIL_THREAD_ID_RE = re.compile(
    rb"X-GM-THRID\s+(\d+)",
    re.IGNORECASE,
)

GMAIL_MESSAGE_ID_RE = re.compile(
    rb"X-GM-MSGID\s+(\d+)",
    re.IGNORECASE,
)

GMAIL_LABELS_RE = re.compile(
    rb"X-GM-LABELS\s*\((.*?)\)\s",
    re.IGNORECASE,
)

LOGGER = logging.getLogger(__name__)


class RealIMAPMailbox:
    def __init__(
        self,
        *,
        host: str,
        port: int,
        username: str,
        password: str,
        mailbox: str = "INBOX",
        use_ssl: bool = True,
        search_criterion: str = "ALL",
        timeout: float = 30.0,
    ) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.mailbox = mailbox
        self.use_ssl = use_ssl
        self.search_criterion = search_criterion
        self.timeout = timeout

    def _connect(self) -> imaplib.IMAP4:
        cls = imaplib.IMAP4_SSL if self.use_ssl else imaplib.IMAP4

        connection = cls(
            self.host,
            self.port,
            timeout=self.timeout,
        )

        connection.login(
            self.username,
            self.password,
        )

        return connection

    @staticmethod
    def _uidvalidity(
        connection: imaplib.IMAP4,
    ) -> int:
        status, values = connection.response("UIDVALIDITY")

        joined = b" ".join(value for value in (values or []) if isinstance(value, bytes))

        match = UIDVALIDITY_RE.search(joined)

        if status != "UIDVALIDITY":
            raise RuntimeError(f"IMAP server did not provide UIDVALIDITY: {status!r} {values!r}")

        # imaplib commonly returns [b"123"], while some
        # servers include the response-code label.
        if joined.strip().isdigit():
            return int(joined.strip())

        if not match:
            raise RuntimeError(f"IMAP server did not provide UIDVALIDITY: {status!r} {values!r}")

        return int(match.group(1))

    @staticmethod
    def _parse_fetch(
        uid: int,
        response: list[bytes | tuple[bytes, bytes] | None],
    ) -> tuple[
        bytes,
        datetime | None,
        tuple[str, ...],
        dict[str, object],
    ]:
        raw: bytes | None = None
        metadata = b""

        for item in response:
            if isinstance(item, tuple):
                metadata += item[0]
                raw = item[1]

            elif isinstance(item, bytes):
                metadata += item

        if raw is None:
            raise RuntimeError(f"UID FETCH {uid} returned no RFC822 payload: {response!r}")

        flags_match = FLAGS_RE.search(metadata)

        flags = (
            tuple(
                flags_match.group(1)
                .decode(
                    "ascii",
                    "replace",
                )
                .split()
            )
            if flags_match
            else ()
        )

        date_match = INTERNALDATE_RE.search(metadata)

        internal_date: datetime | None = None

        if date_match:
            try:
                internal_date = parsedate_to_datetime(
                    date_match.group(1).decode("ascii")
                ).astimezone(UTC)

            except (
                TypeError,
                ValueError,
                OverflowError,
            ):
                internal_date = None

        provider_metadata: dict[str, object] = {}

        thread_match = GMAIL_THREAD_ID_RE.search(metadata)

        message_match = GMAIL_MESSAGE_ID_RE.search(metadata)

        labels_match = GMAIL_LABELS_RE.search(metadata)

        if thread_match:
            provider_metadata["gmail_thread_id"] = thread_match.group(1).decode("ascii")

        if message_match:
            provider_metadata["gmail_message_id"] = message_match.group(1).decode("ascii")

        if labels_match:
            labels_text = labels_match.group(1).decode(
                "utf-8",
                "replace",
            )

            try:
                provider_metadata["gmail_labels"] = shlex.split(labels_text)

            except ValueError:
                provider_metadata["gmail_labels"] = [labels_text]

        return (
            raw,
            internal_date,
            flags,
            provider_metadata,
        )

    @staticmethod
    def _supports_gmail_extensions(
        connection: imaplib.IMAP4,
    ) -> bool:
        capabilities = getattr(
            connection,
            "capabilities",
            (),
        )

        return any(
            (
                item.decode(
                    "ascii",
                    "ignore",
                )
                if isinstance(item, bytes)
                else str(item)
            ).upper()
            == "X-GM-EXT-1"
            for item in capabilities
        )

    @staticmethod
    def _quoted_mailbox(
        mailbox: str,
    ) -> str:
        """Encode a mailbox as a safe IMAP quoted string."""

        if "\r" in mailbox or "\n" in mailbox:
            raise ValueError("IMAP mailbox names cannot contain line breaks")

        escaped = mailbox.replace("\\", "\\\\").replace('"', '\\"')

        return f'"{escaped}"'

    def fetch_candidates(
        self,
    ) -> list[MailboxMessage]:
        connection = self._connect()

        try:
            # Read-only is deliberate. Fetching must not itself
            # acknowledge the email.
            status, select_data = connection.select(
                self._quoted_mailbox(self.mailbox),
                readonly=True,
            )

            if status != "OK":
                raise RuntimeError(f"cannot select mailbox {self.mailbox!r}: {select_data!r}")

            uidvalidity = self._uidvalidity(connection)

            gmail_extensions = self._supports_gmail_extensions(connection)

            # imaplib requires None here to omit the
            # optional SEARCH charset.
            search_charset: Any = None

            status, search_data = connection.uid(
                "SEARCH",
                search_charset,
                self.search_criterion,
            )

            if status != "OK":
                raise RuntimeError(f"UID SEARCH failed: {search_data!r}")

            uid_blob = b" ".join(value for value in search_data if isinstance(value, bytes))

            messages: list[MailboxMessage] = []

            for token in uid_blob.split():
                uid = int(token)

                try:
                    fields = "BODY.PEEK[] INTERNALDATE FLAGS UID"

                    if gmail_extensions:
                        fields += " X-GM-THRID X-GM-MSGID X-GM-LABELS"

                    fetch_status, fetch_data = connection.uid(
                        "FETCH",
                        str(uid),
                        f"({fields})",
                    )

                    if fetch_status != "OK":
                        raise RuntimeError(f"UID FETCH {uid} failed: {fetch_data!r}")

                    (
                        raw,
                        internal_date,
                        flags,
                        provider_metadata,
                    ) = self._parse_fetch(
                        uid,
                        fetch_data,
                    )

                except (
                    RuntimeError,
                    TypeError,
                    ValueError,
                ):
                    # The UID remains discoverable on the
                    # next poll; later UIDs still progress.
                    LOGGER.exception(
                        "one UID FETCH failed; continuing",
                        extra={
                            "imap_uid": uid,
                        },
                    )
                    continue

                messages.append(
                    MailboxMessage(
                        mailbox=self.mailbox,
                        uidvalidity=uidvalidity,
                        uid=uid,
                        raw_message=raw,
                        internal_date=internal_date,
                        flags=flags,
                        provider_metadata=(provider_metadata),
                    )
                )

            return messages

        finally:
            # A timed-out socket can raise during LOGOUT.
            with suppress(
                imaplib.IMAP4.error,
                OSError,
            ):
                connection.logout()

    def mark_seen(
        self,
        uid: int,
        *,
        uidvalidity: int | None = None,
    ) -> None:
        """Mark one durably handled IMAP UID as seen.

        Candidate fetching remains read-only and uses
        BODY.PEEK[]. This method opens a separate read-write
        mailbox selection only after processing has completed.
        """

        connection = self._connect()

        try:
            status, select_data = connection.select(
                self._quoted_mailbox(self.mailbox),
                readonly=False,
            )

            if status != "OK":
                raise RuntimeError(f"cannot select mailbox {self.mailbox!r}: {select_data!r}")

            current_uidvalidity = self._uidvalidity(connection)

            # A UID may identify a different message after
            # UIDVALIDITY changes. Refuse to acknowledge in
            # that situation.
            if uidvalidity is not None and current_uidvalidity != uidvalidity:
                raise RuntimeError(
                    "refusing to mark IMAP UID as seen "
                    "because UIDVALIDITY changed: "
                    f"expected {uidvalidity}, "
                    f"received {current_uidvalidity}"
                )

            store_status, store_data = connection.uid(
                "STORE",
                str(uid),
                "+FLAGS.SILENT",
                r"(\Seen)",
            )

            if store_status != "OK":
                raise RuntimeError(
                    f"cannot mark IMAP UID {uid} as seen: UID STORE failed with {store_data!r}"
                )

            LOGGER.info(
                "IMAP message marked as seen",
                extra={
                    "imap_uid": uid,
                    "uidvalidity": (current_uidvalidity),
                    "mailbox": self.mailbox,
                },
            )

        finally:
            with suppress(
                imaplib.IMAP4.error,
                OSError,
            ):
                connection.logout()

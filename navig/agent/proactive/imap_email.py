"""
IMAP Email Provider

Standard email integration using IMAP for reading and SMTP for sending.
Works with Gmail, Outlook, Fastmail, self-hosted mail servers, etc.
"""

import asyncio
import email
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from imaplib import IMAP4_SSL
from smtplib import SMTP_SSL
from typing import List, Optional

from navig.agent.proactive.providers import EmailProvider, EmailMessage


class IMAPEmailProvider(EmailProvider):
    """
    IMAP/SMTP email provider.
    
    Usage:
        provider = IMAPEmailProvider(
            imap_host="imap.gmail.com",
            smtp_host="smtp.gmail.com",
            email="you@gmail.com",
            password="app-password-here"  # Use app passwords for Gmail
        )
    """
    
    def __init__(
        self,
        imap_host: str,
        smtp_host: str,
        email_address: str,
        password: str,
        imap_port: int = 993,
        smtp_port: int = 465
    ):
        """
        Initialize IMAP/SMTP provider.
        
        Args:
            imap_host: IMAP server hostname
            smtp_host: SMTP server hostname
            email_address: Email address for login
            password: Password or app-specific password
            imap_port: IMAP port (default: 993 for SSL)
            smtp_port: SMTP port (default: 465 for SSL)
        """
        self.imap_host = imap_host
        self.smtp_host = smtp_host
        self.email_address = email_address
        self.password = password
        self.imap_port = imap_port
        self.smtp_port = smtp_port
        
    async def list_unread(self, limit: int = 10) -> List[EmailMessage]:
        """
        Fetch unread emails from inbox.
        
        Args:
            limit: Maximum number of messages to return
            
        Returns:
            List of unread EmailMessage objects
        """
        def _fetch():
            messages = []
            
            with IMAP4_SSL(self.imap_host, self.imap_port) as imap:
                imap.login(self.email_address, self.password)
                imap.select('INBOX')
                
                # Search for unread messages
                status, data = imap.search(None, 'UNSEEN')
                if status != 'OK':
                    return messages
                    
                message_ids = data[0].split()
                
                # Get most recent messages up to limit
                for msg_id in message_ids[-limit:][::-1]:
                    status, msg_data = imap.fetch(msg_id, '(RFC822)')
                    if status != 'OK':
                        continue
                        
                    raw_email = msg_data[0][1]
                    msg = email.message_from_bytes(raw_email)
                    
                    # Extract date
                    date_str = msg.get('Date', '')
                    try:
                        from email.utils import parsedate_to_datetime
                        received_at = parsedate_to_datetime(date_str)
                    except Exception:
                        received_at = datetime.now()
                    
                    # Extract snippet from body
                    snippet = self._extract_snippet(msg)
                    
                    messages.append(EmailMessage(
                        id=msg_id.decode('utf-8'),
                        subject=msg.get('Subject', '(No Subject)'),
                        sender=msg.get('From', 'Unknown'),
                        snippet=snippet,
                        received_at=received_at,
                        read=False
                    ))
                    
            return messages
        
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _fetch)
    
    async def draft_email(
        self, 
        to: List[str], 
        subject: str, 
        body: str
    ) -> str:
        """
        Create a draft email (saves to Drafts folder via IMAP APPEND).
        
        Args:
            to: List of recipient email addresses
            subject: Email subject
            body: Email body (plain text)
            
        Returns:
            Message ID of the draft
        """
        def _create_draft():
            # Build message
            msg = MIMEMultipart()
            msg['From'] = self.email_address
            msg['To'] = ', '.join(to)
            msg['Subject'] = subject
            msg.attach(MIMEText(body, 'plain'))
            
            with IMAP4_SSL(self.imap_host, self.imap_port) as imap:
                imap.login(self.email_address, self.password)
                
                # Find drafts folder
                status, folders = imap.list()
                drafts_folder = 'Drafts'
                
                for folder in folders:
                    folder_name = folder.decode('utf-8')
                    if '\\Drafts' in folder_name or 'Drafts' in folder_name:
                        # Extract folder name
                        parts = folder_name.split('"')
                        if len(parts) >= 2:
                            drafts_folder = parts[-2]
                            break
                
                # Append to drafts
                result = imap.append(
                    drafts_folder,
                    '\\Draft',
                    None,
                    msg.as_bytes()
                )
                
                return f"draft-{datetime.now().timestamp()}"
        
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _create_draft)
    
    async def send_email(
        self,
        to: List[str],
        subject: str,
        body: str,
        html_body: Optional[str] = None
    ) -> bool:
        """
        Send email via SMTP.
        
        Args:
            to: List of recipient addresses
            subject: Email subject
            body: Plain text body
            html_body: Optional HTML body
            
        Returns:
            True if sent successfully
        """
        def _send():
            msg = MIMEMultipart('alternative')
            msg['From'] = self.email_address
            msg['To'] = ', '.join(to)
            msg['Subject'] = subject
            
            msg.attach(MIMEText(body, 'plain'))
            if html_body:
                msg.attach(MIMEText(html_body, 'html'))
            
            with SMTP_SSL(self.smtp_host, self.smtp_port) as smtp:
                smtp.login(self.email_address, self.password)
                smtp.send_message(msg)
                
            return True
        
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _send)
    
    def _extract_snippet(self, msg, max_length: int = 150) -> str:
        """Extract plain text snippet from email."""
        body = ""
        
        if msg.is_multipart():
            for part in msg.walk():
                content_type = part.get_content_type()
                if content_type == 'text/plain':
                    try:
                        body = part.get_payload(decode=True).decode('utf-8', errors='replace')
                        break
                    except Exception:
                        continue
        else:
            try:
                body = msg.get_payload(decode=True).decode('utf-8', errors='replace')
            except Exception:
                body = ""
        
        # Clean up and truncate
        body = body.strip().replace('\r\n', ' ').replace('\n', ' ')
        if len(body) > max_length:
            body = body[:max_length] + '...'
            
        return body


# Pre-configured providers for common services
class GmailProvider(IMAPEmailProvider):
    """Gmail-specific provider with default settings."""
    
    def __init__(self, email_address: str, app_password: str):
        """
        Initialize Gmail provider.
        
        Note: Requires an App Password, not your regular password.
        Generate at: https://myaccount.google.com/apppasswords
        """
        super().__init__(
            imap_host="imap.gmail.com",
            smtp_host="smtp.gmail.com",
            email_address=email_address,
            password=app_password,
            imap_port=993,
            smtp_port=465
        )


class OutlookProvider(IMAPEmailProvider):
    """Outlook/Office 365 provider with default settings."""
    
    def __init__(self, email_address: str, password: str):
        super().__init__(
            imap_host="outlook.office365.com",
            smtp_host="smtp.office365.com",
            email_address=email_address,
            password=password,
            imap_port=993,
            smtp_port=587  # Outlook uses STARTTLS on 587
        )


class FastmailProvider(IMAPEmailProvider):
    """Fastmail provider with default settings."""
    
    def __init__(self, email_address: str, app_password: str):
        super().__init__(
            imap_host="imap.fastmail.com",
            smtp_host="smtp.fastmail.com",
            email_address=email_address,
            password=app_password,
            imap_port=993,
            smtp_port=465
        )


# =============================================================================
# Provider Factory
# =============================================================================

_PROVIDER_MAP = {
    'gmail': GmailProvider,
    'outlook': OutlookProvider,
    'fastmail': FastmailProvider,
}


def get_email_provider(
    provider_name: str,
    email_address: str,
    password: str,
    host: Optional[str] = None,
    port: Optional[int] = None,
) -> IMAPEmailProvider:
    """
    Factory to create an email provider by name.

    Args:
        provider_name: 'gmail', 'outlook', 'fastmail', or 'imap' (generic)
        email_address: Login email
        password: Password or app password
        host: IMAP host (only for 'imap' provider)
        port: IMAP port (only for 'imap' provider)

    Returns:
        Configured IMAPEmailProvider instance
    """
    name = provider_name.lower().strip()

    if name in _PROVIDER_MAP:
        return _PROVIDER_MAP[name](email_address, password)

    if name == 'imap':
        if not host:
            raise ValueError("IMAP provider requires 'host' parameter")
        return IMAPEmailProvider(
            imap_host=host,
            smtp_host=host.replace('imap', 'smtp'),
            email_address=email_address,
            password=password,
            imap_port=port or 993,
        )

    raise ValueError(
        f"Unknown email provider: '{provider_name}'. "
        f"Supported: {', '.join(list(_PROVIDER_MAP.keys()) + ['imap'])}"
    )

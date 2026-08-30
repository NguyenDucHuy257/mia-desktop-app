from __future__ import annotations

import base64
import json
import os
import secrets
from dataclasses import asdict

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.session_manager.models import CredentialBundle


SESSION_ENCRYPTION_KEY_ENV = 'MIA_SESSION_ENCRYPTION_KEY'
SESSION_ENCRYPTION_KEY_ID_ENV = 'MIA_SESSION_ENCRYPTION_KEY_ID'
DEFAULT_KEY_ID = 'primary'
_NONCE_BYTES = 12


class EncryptionConfigurationError(ValueError):
    pass


class CiphertextIntegrityError(ValueError):
    pass


class SessionCipher:
    """AES-256-GCM envelope using a key supplied outside the database."""

    def __init__(self, key: bytes, *, key_id: str = DEFAULT_KEY_ID) -> None:
        if len(key) != 32:
            raise EncryptionConfigurationError('session encryption key must be 32 bytes')
        if not key_id or ':' in key_id:
            raise EncryptionConfigurationError('session encryption key ID is invalid')
        self._aead = AESGCM(key)
        self.key_id = key_id

    @classmethod
    def from_environment(cls) -> SessionCipher:
        encoded = os.getenv(SESSION_ENCRYPTION_KEY_ENV, '').strip()
        if not encoded:
            raise EncryptionConfigurationError(
                f'{SESSION_ENCRYPTION_KEY_ENV} is required'
            )
        try:
            key = _urlsafe_decode(encoded)
        except (ValueError, TypeError) as error:
            raise EncryptionConfigurationError(
                f'{SESSION_ENCRYPTION_KEY_ENV} must be URL-safe base64'
            ) from error
        return cls(
            key,
            key_id=os.getenv(SESSION_ENCRYPTION_KEY_ID_ENV, DEFAULT_KEY_ID).strip(),
        )

    @staticmethod
    def generate_key() -> str:
        return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode('ascii')

    def encrypt_text(self, plaintext: str, *, purpose: str, subject: str) -> str:
        if not isinstance(plaintext, str) or not plaintext:
            raise ValueError('plaintext must be a non-empty string')
        nonce = secrets.token_bytes(_NONCE_BYTES)
        ciphertext = self._aead.encrypt(
            nonce,
            plaintext.encode('utf-8'),
            _aad(purpose, subject, self.key_id),
        )
        return base64.urlsafe_b64encode(nonce + ciphertext).decode('ascii')

    def decrypt_text(
        self,
        envelope: str,
        *,
        purpose: str,
        subject: str,
        key_id: str,
    ) -> str:
        if key_id != self.key_id:
            raise EncryptionConfigurationError(
                f'encryption key ID {key_id!r} is not configured'
            )
        try:
            raw = _urlsafe_decode(envelope)
            if len(raw) <= _NONCE_BYTES:
                raise ValueError('ciphertext envelope is too short')
            plaintext = self._aead.decrypt(
                raw[:_NONCE_BYTES],
                raw[_NONCE_BYTES:],
                _aad(purpose, subject, key_id),
            )
            return plaintext.decode('utf-8')
        except (InvalidTag, UnicodeDecodeError, ValueError) as error:
            raise CiphertextIntegrityError('encrypted session value is invalid') from error

    def encrypt_credentials(self, credentials: CredentialBundle, *, subject: str) -> str:
        payload = json.dumps(
            asdict(credentials),
            ensure_ascii=False,
            separators=(',', ':'),
            sort_keys=True,
        )
        return self.encrypt_text(payload, purpose='credential', subject=subject)

    def decrypt_credentials(
        self,
        envelope: str,
        *,
        subject: str,
        key_id: str,
    ) -> CredentialBundle:
        try:
            payload = json.loads(self.decrypt_text(
                envelope,
                purpose='credential',
                subject=subject,
                key_id=key_id,
            ))
        except json.JSONDecodeError as error:
            raise CiphertextIntegrityError(
                'encrypted credential payload is invalid'
            ) from error
        if not isinstance(payload, dict):
            raise CiphertextIntegrityError('encrypted credential payload is invalid')
        try:
            return CredentialBundle(
                account_key=str(payload['account_key']),
                username=str(payload['username']),
                password=str(payload['password']),
                proxy_url=(
                    str(payload['proxy_url'])
                    if payload.get('proxy_url') is not None
                    else None
                ),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise CiphertextIntegrityError(
                'encrypted credential payload is invalid'
            ) from error


def _aad(purpose: str, subject: str, key_id: str) -> bytes:
    return f'mia-session:v1:{purpose}:{subject}:{key_id}'.encode('utf-8')


def _urlsafe_decode(value: str) -> bytes:
    padding = '=' * (-len(value) % 4)
    return base64.b64decode(
        value + padding,
        altchars=b'-_',
        validate=True,
    )

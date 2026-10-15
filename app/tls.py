"""A certificate for the phone, so the link to it is private and can be an app.

Two things turn on this, and both were missing while the phone was served over
plain http://:

* Everything crossed your Wi-Fi in the clear. An app this careful about keeping
  your messages on one machine was reading them out loud on the way to your
  pocket.
* Browsers refuse service workers, caches and most of what makes a page behave
  like an app anywhere but a secure origin. Measured on this network:
  `isSecureContext` false, `serviceWorker` undefined, `caches` undefined. No
  amount of care in the page could have worked around that.

The certificate is made once, kept with the vault, and covers this Mac's name
and its addresses. iOS has to be told to trust it once -- the pairing page
hands it over and says how -- and it is yours alone: the key never leaves here
and nothing else will ever accept it.
"""
from __future__ import annotations

import datetime as dt
import ipaddress
from pathlib import Path

CERT_DAYS = 825                 # the longest iOS will accept for a leaf certificate


def paths(root: Path) -> tuple[Path, Path]:
    return root / "phone-cert.pem", root / "phone-key.pem"


def ensure(root: Path, names: list[str]) -> tuple[Path, Path]:
    """The certificate and key for serving to the phone, made if absent.

    Remade when this Mac's addresses change, because a certificate that does
    not name the address you typed is one iOS will refuse however well you
    trusted it.
    """
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    cert_path, key_path = paths(root)
    if cert_path.exists() and key_path.exists() and _covers(cert_path, names):
        return cert_path, key_path

    root.mkdir(parents=True, exist_ok=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    who = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Messaging Graph on this Mac")])
    alt: list[x509.GeneralName] = []
    for name in names:
        try:
            alt.append(x509.IPAddress(ipaddress.ip_address(name)))
        except ValueError:
            alt.append(x509.DNSName(name))
    now = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder()
            .subject_name(who).issuer_name(who)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(minutes=5))
            .not_valid_after(now + dt.timedelta(days=CERT_DAYS))
            .add_extension(x509.SubjectAlternativeName(alt), critical=False)
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]),
                           critical=False)
            .sign(key, hashes.SHA256()))
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption()))
    key_path.chmod(0o600)
    cert_path.chmod(0o644)      # the phone has to be able to download it
    return cert_path, key_path


def _covers(cert_path: Path, names: list[str]) -> bool:
    from cryptography import x509
    try:
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        have = {str(v) for v in san.get_values_for_type(x509.DNSName)}
        have |= {str(v) for v in san.get_values_for_type(x509.IPAddress)}
        if cert.not_valid_after_utc <= dt.datetime.now(dt.timezone.utc):
            return False
        return set(names) <= have
    except Exception:
        return False

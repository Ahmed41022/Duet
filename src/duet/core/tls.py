"""Create and use Duet's per-user HTTPS trust root and short-lived server identity."""

import ipaddress
import os
import ssl
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from duet.core._network import is_private_ipv4
from duet.core.outbox import default_database

_ROOT_PRIVATE = "root-private.pem"
_ROOT_PUBLIC = "duet-root.cer"


@dataclass(frozen=True, slots=True)
class TLSIdentity:
    context: ssl.SSLContext
    root_certificate: Path
    fingerprint: str


def sharing_url(host: str, port: int, *, https: bool = False) -> str:
    """Return the browser origin for a Duet listener."""
    scheme, default_port = ("https", 443) if https else ("http", 80)
    authority = host if port == default_port else f"{host}:{port}"
    return f"{scheme}://{authority}"


def pairing_url(base_url: str, token: str, fingerprint: str | None = None) -> str:
    """Keep the offer and optional root fingerprint out of server requests."""
    fields = {"token": token}
    if fingerprint is not None:
        fields["fingerprint"] = fingerprint.replace(":", "").lower()
    return f"{base_url}/#{urlencode(fields)}"


def _common_name(value: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, value)])


def _key_bytes(key: ec.EllipticCurvePrivateKey) -> bytes:
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


def _new_root(now: datetime) -> bytes:
    key = ec.generate_private_key(ec.SECP256R1())
    name = _common_name("Duet Local Root")
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(hours=1))
        .not_valid_after(now + timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(False, False, False, False, False, True, True, False, False),
            critical=True,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(key.public_key()),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    return certificate.public_bytes(serialization.Encoding.PEM) + _key_bytes(key)


def _write_staged(directory: Path, data: bytes) -> Path:
    with tempfile.NamedTemporaryFile(dir=directory, delete=False) as staged:
        path = Path(staged.name)
        try:
            staged.write(data)
            staged.flush()
            os.fsync(staged.fileno())
        except BaseException:
            staged.close()
            path.unlink(missing_ok=True)
            raise
    return path


def _publish_once(path: Path, data: bytes) -> None:
    staged = _write_staged(path.parent, data)
    try:
        # Publish the entire file without replacing the winner of a concurrent
        # first start. Windows rename has no-clobber semantics.
        if os.name == "nt":
            staged.rename(path)
        else:
            os.link(staged, path)
    except FileExistsError:
        pass
    finally:
        staged.unlink(missing_ok=True)


def _load_root(
    directory: Path, now: datetime
) -> tuple[x509.Certificate, ec.EllipticCurvePrivateKey]:
    private_path = directory / _ROOT_PRIVATE
    if not private_path.exists():
        _publish_once(private_path, _new_root(now))
    if os.name != "nt":
        private_path.chmod(0o600)
    data = private_path.read_bytes()
    try:
        root = x509.load_pem_x509_certificate(data)
        root.verify_directly_issued_by(root)
        key = serialization.load_pem_private_key(data, password=None)
        if not isinstance(key, ec.EllipticCurvePrivateKey) or key.public_key() != (
            root.public_key()
        ):
            raise ValueError("Certificate and key differ")
    except (InvalidSignature, TypeError, ValueError) as exc:
        raise ValueError("Duet's stored TLS root is invalid") from exc
    if not root.not_valid_before_utc <= now < root.not_valid_after_utc:
        raise ValueError("Duet's TLS root is outside its validity period")
    return root, key


def _server_bundle(
    host: str,
    now: datetime,
    root: x509.Certificate,
    root_key: ec.EllipticCurvePrivateKey,
) -> bytes:
    key = ec.generate_private_key(ec.SECP256R1())
    leaf = (
        x509.CertificateBuilder()
        .subject_name(_common_name(host))
        .issuer_name(root.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(min(now + timedelta(days=90), root.not_valid_after_utc))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.IPv4Address(host))]),
            critical=False,
        )
        .add_extension(
            x509.KeyUsage(True, False, False, False, False, False, False, False, False),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(root_key.public_key()),
            critical=False,
        )
        .sign(root_key, hashes.SHA256())
    )
    return leaf.public_bytes(serialization.Encoding.PEM) + _key_bytes(key)


def prepare_tls(host: str, *, directory: Path | None = None) -> TLSIdentity:
    """Load or create the installed trust root and issue a leaf for this address."""
    if not is_private_ipv4(host):
        raise ValueError("HTTPS requires a private RFC 1918 IPv4 address")
    if directory is None:
        directory = default_database().parent / "tls"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name != "nt":
        directory.chmod(0o700)
    now = datetime.now(UTC)
    root, root_key = _load_root(directory, now)
    public_path = directory / _ROOT_PUBLIC
    public_bytes = root.public_bytes(serialization.Encoding.DER)
    if not public_path.exists():
        _publish_once(public_path, public_bytes)
    if public_path.read_bytes() != public_bytes:
        raise ValueError(
            f"Duet's public certificate differs from its root. Remove {public_path} "
            "and restart to export a fresh copy."
        )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    staged = _write_staged(directory, _server_bundle(host, now, root, root_key))
    try:
        context.load_cert_chain(staged)
    finally:
        staged.unlink()
    fingerprint = root.fingerprint(hashes.SHA256()).hex(":").upper()
    return TLSIdentity(context, public_path, fingerprint)

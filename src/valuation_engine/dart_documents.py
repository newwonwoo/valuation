from __future__ import annotations

import codecs
from dataclasses import dataclass, field
from datetime import date
from hashlib import sha256
from io import BytesIO
import json
from pathlib import PurePosixPath
import re
from typing import Callable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
import xml.etree.ElementTree as ET
from zipfile import BadZipFile, ZipFile

from .live_indexers import require_env_credential
from .source_index import DocumentIndexRecord, stable_hash


FetchBytes = Callable[[str], bytes]

_TEXT_EXTENSIONS = frozenset({".xml", ".html", ".htm", ".xhtml", ".txt"})
_DEFAULT_MAX_FILES = 256
_DEFAULT_MAX_ARCHIVE_BYTES = 24_000_000
_DEFAULT_MAX_MEMBER_BYTES = 12_000_000
_DEFAULT_MAX_TOTAL_UNCOMPRESSED_BYTES = 80_000_000
_DEFAULT_MAX_COMPRESSION_RATIO = 200.0
_SENSITIVE_QUERY_KEYS = frozenset(
    {"crtfc_key", "api_key", "apikey", "access_token", "token", "authorization"}
)


class DartDocumentError(ValueError):
    pass


class DartDocumentFetchError(RuntimeError):
    pass


@dataclass(frozen=True)
class DartDocumentMember:
    path: str
    size_bytes: int
    compressed_size_bytes: int
    content_hash: str
    media_type: str
    text_encoding: str | None
    text: str | None

    @property
    def is_text(self) -> bool:
        return self.text is not None

    def validate(self) -> None:
        if not self.path or self.size_bytes < 0 or self.compressed_size_bytes < 0:
            raise DartDocumentError("DART document member has invalid path/size")
        if not re.fullmatch(r"[0-9a-f]{64}", self.content_hash):
            raise DartDocumentError(
                f"DART document member {self.path} has invalid content hash"
            )
        if not self.media_type:
            raise DartDocumentError(
                f"DART document member {self.path} requires media_type"
            )
        if self.text is None and self.text_encoding is not None:
            raise DartDocumentError(
                f"binary DART document member {self.path} cannot declare text encoding"
            )
        if self.text is not None and not self.text_encoding:
            raise DartDocumentError(
                f"text DART document member {self.path} requires encoding"
            )


@dataclass(frozen=True)
class DartOriginalFilingDocument:
    rcept_no: str
    checked_at: date
    source_ref: str
    archive_hash: str
    archive_size_bytes: int
    members: tuple[DartDocumentMember, ...]
    published_at: date | None = None
    index_record_hash: str | None = None
    archive_bytes: bytes = field(default=b"", repr=False)

    def validate(self) -> None:
        _validate_rcept_no(self.rcept_no)
        if not self.source_ref or not self.archive_hash or self.archive_size_bytes <= 0:
            raise DartDocumentError(
                "DART original filing requires source_ref, archive hash and positive size"
            )
        if not re.fullmatch(r"[0-9a-f]{64}", self.archive_hash):
            raise DartDocumentError("DART original filing archive_hash is invalid")
        if len(self.archive_bytes) != self.archive_size_bytes:
            raise DartDocumentError(
                "DART original filing retained archive size does not match manifest"
            )
        if sha256(self.archive_bytes).hexdigest() != self.archive_hash:
            raise DartDocumentError(
                "DART original filing retained archive hash does not match manifest"
            )
        if self.published_at is not None and self.published_at > self.checked_at:
            raise DartDocumentError(
                "DART filing cannot be retrieved before its indexed publication date"
            )
        if self.index_record_hash is not None and not re.fullmatch(
            r"[0-9a-f]{64}", self.index_record_hash
        ):
            raise DartDocumentError("DART index_record_hash is invalid")
        if not self.members:
            raise DartDocumentError("DART original filing contains no members")
        paths = tuple(member.path for member in self.members)
        if len(paths) != len(set(paths)):
            raise DartDocumentError("DART original filing contains duplicate member paths")
        for member in self.members:
            member.validate()
        if not self.text_members:
            raise DartDocumentError("DART original filing contains no supported text member")
        _validate_members_against_retained_archive(self)

    @property
    def text_members(self) -> tuple[DartDocumentMember, ...]:
        return tuple(member for member in self.members if member.is_text)

    @property
    def manifest_hash(self) -> str:
        payload = {
            "rcept_no": self.rcept_no,
            "checked_at": self.checked_at.isoformat(),
            "published_at": self.published_at.isoformat() if self.published_at else None,
            "source_ref": self.source_ref,
            "archive_hash": self.archive_hash,
            "archive_size_bytes": self.archive_size_bytes,
            "index_record_hash": self.index_record_hash,
            "members": [
                {
                    "path": member.path,
                    "size_bytes": member.size_bytes,
                    "compressed_size_bytes": member.compressed_size_bytes,
                    "content_hash": member.content_hash,
                    "media_type": member.media_type,
                    "text_encoding": member.text_encoding,
                }
                for member in self.members
            ],
        }
        return sha256(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()


@dataclass(frozen=True)
class DartDocumentFetchPolicy:
    max_files: int = _DEFAULT_MAX_FILES
    max_archive_bytes: int = _DEFAULT_MAX_ARCHIVE_BYTES
    max_member_bytes: int = _DEFAULT_MAX_MEMBER_BYTES
    max_total_uncompressed_bytes: int = _DEFAULT_MAX_TOTAL_UNCOMPRESSED_BYTES
    max_compression_ratio: float = _DEFAULT_MAX_COMPRESSION_RATIO

    def validate(self) -> None:
        if self.max_files <= 0:
            raise DartDocumentError("max_files must be positive")
        if (
            self.max_archive_bytes <= 0
            or self.max_member_bytes <= 0
            or self.max_total_uncompressed_bytes <= 0
        ):
            raise DartDocumentError("DART document byte limits must be positive")
        if self.max_total_uncompressed_bytes < self.max_member_bytes:
            raise DartDocumentError(
                "max_total_uncompressed_bytes must be >= max_member_bytes"
            )
        if self.max_compression_ratio <= 1:
            raise DartDocumentError("max_compression_ratio must exceed 1")


def build_opendart_document_url(
    *,
    rcept_no: str,
    api_key: str | None = None,
) -> str:
    _validate_rcept_no(rcept_no)
    key = api_key or require_env_credential("DART_API_KEY")
    return "https://opendart.fss.or.kr/api/document.xml?" + urlencode(
        {"crtfc_key": key, "rcept_no": rcept_no}
    )


def opendart_document_source_ref(rcept_no: str) -> str:
    _validate_rcept_no(rcept_no)
    return (
        "https://opendart.fss.or.kr/api/document.xml?"
        + urlencode({"rcept_no": rcept_no})
    )


def parse_opendart_original_document_archive(
    payload: bytes,
    *,
    rcept_no: str,
    checked_at: date,
    source_ref: str | None = None,
    published_at: date | None = None,
    index_record_hash: str | None = None,
    policy: DartDocumentFetchPolicy = DartDocumentFetchPolicy(),
) -> DartOriginalFilingDocument:
    _validate_rcept_no(rcept_no)
    policy.validate()
    if not isinstance(payload, bytes):
        raise DartDocumentFetchError("OpenDART document payload must be bytes")
    if not payload:
        raise DartDocumentFetchError("OpenDART document response is empty")
    if len(payload) > policy.max_archive_bytes:
        raise DartDocumentFetchError(
            f"OpenDART document ZIP exceeds max_archive_bytes={policy.max_archive_bytes}"
        )
    if published_at is not None and published_at > checked_at:
        raise DartDocumentFetchError(
            "OpenDART indexed filing publication date is later than checked_at"
        )
    archive_hash = sha256(payload).hexdigest()

    try:
        archive = ZipFile(BytesIO(payload))
    except BadZipFile as exc:
        status, message = _parse_error_payload(payload)
        if status is not None:
            raise DartDocumentFetchError(
                f"OpenDART document API returned status={status} message={message or ''}"
            ) from exc
        raise DartDocumentFetchError(
            "OpenDART document response is not a valid ZIP archive"
        ) from exc

    members: list[DartDocumentMember] = []
    total_uncompressed = 0
    seen_paths: set[str] = set()
    with archive:
        infos = tuple(info for info in archive.infolist() if not info.is_dir())
        if not infos:
            raise DartDocumentFetchError("OpenDART document ZIP contains no files")
        if len(infos) > policy.max_files:
            raise DartDocumentFetchError(
                f"OpenDART document ZIP exceeds max_files={policy.max_files}"
            )
        for info in infos:
            path = _safe_member_path(info.filename)
            if path in seen_paths:
                raise DartDocumentFetchError(
                    f"OpenDART document ZIP contains duplicate member path: {path}"
                )
            seen_paths.add(path)
            if info.flag_bits & 0x1:
                raise DartDocumentFetchError(
                    f"encrypted OpenDART ZIP member is not supported: {path}"
                )
            if info.file_size > policy.max_member_bytes:
                raise DartDocumentFetchError(
                    f"OpenDART ZIP member exceeds max_member_bytes: {path}"
                )
            total_uncompressed += info.file_size
            if total_uncompressed > policy.max_total_uncompressed_bytes:
                raise DartDocumentFetchError(
                    "OpenDART ZIP exceeds max_total_uncompressed_bytes"
                )
            if info.compress_size == 0:
                compression_ratio = float("inf") if info.file_size else 1.0
            else:
                compression_ratio = info.file_size / info.compress_size
            if compression_ratio > policy.max_compression_ratio:
                raise DartDocumentFetchError(
                    f"OpenDART ZIP member compression ratio is suspicious: {path}"
                )
            try:
                raw = archive.read(info)
            except (BadZipFile, NotImplementedError, RuntimeError) as exc:
                raise DartDocumentFetchError(
                    f"OpenDART ZIP member cannot be read: {path}"
                ) from exc
            if len(raw) != info.file_size:
                raise DartDocumentFetchError(
                    f"OpenDART ZIP member size mismatch: {path}"
                )
            extension = PurePosixPath(path).suffix.lower()
            text = None
            encoding = None
            if extension in _TEXT_EXTENSIONS:
                encoding = _detect_text_encoding(raw)
                try:
                    text = raw.decode(encoding, errors="strict")
                except (UnicodeDecodeError, LookupError) as exc:
                    raise DartDocumentFetchError(
                        f"OpenDART text member cannot be decoded as {encoding}: {path}"
                    ) from exc
            members.append(
                DartDocumentMember(
                    path=path,
                    size_bytes=info.file_size,
                    compressed_size_bytes=info.compress_size,
                    content_hash=sha256(raw).hexdigest(),
                    media_type=_media_type(extension),
                    text_encoding=encoding,
                    text=text,
                )
            )

    result = DartOriginalFilingDocument(
        rcept_no=rcept_no,
        checked_at=checked_at,
        published_at=published_at,
        source_ref=_sanitize_source_ref(
            source_ref or opendart_document_source_ref(rcept_no),
            expected_rcept_no=rcept_no,
        ),
        archive_hash=archive_hash,
        archive_size_bytes=len(payload),
        index_record_hash=index_record_hash,
        members=tuple(sorted(members, key=lambda item: item.path)),
        archive_bytes=payload,
    )
    result.validate()
    return result


def fetch_opendart_original_document(
    fetch_bytes: FetchBytes,
    *,
    rcept_no: str,
    checked_at: date,
    api_key: str | None = None,
    policy: DartDocumentFetchPolicy = DartDocumentFetchPolicy(),
) -> DartOriginalFilingDocument:
    url = build_opendart_document_url(rcept_no=rcept_no, api_key=api_key)
    payload = fetch_bytes(url)
    if not isinstance(payload, bytes):
        raise DartDocumentFetchError("OpenDART binary transport must return bytes")
    return parse_opendart_original_document_archive(
        payload,
        rcept_no=rcept_no,
        checked_at=checked_at,
        source_ref=opendart_document_source_ref(rcept_no),
        policy=policy,
    )


def fetch_indexed_opendart_original_document(
    fetch_bytes: FetchBytes,
    record: DocumentIndexRecord,
    *,
    checked_at: date,
    api_key: str | None = None,
    policy: DartDocumentFetchPolicy = DartDocumentFetchPolicy(),
) -> DartOriginalFilingDocument:
    record.validate()
    if record.source_id != "KR_OPENDART":
        raise DartDocumentError(
            f"indexed document source must be KR_OPENDART, got {record.source_id}"
        )
    rcept_no = _receipt_number_from_index_record(record)
    expected_document_id = f"DART_{rcept_no}"
    if record.document_id != expected_document_id:
        raise DartDocumentError(
            "OpenDART index record document_id does not match its receipt number"
        )
    if record.published_at is None:
        raise DartDocumentError(
            "OpenDART index record requires published_at before original-document fetch"
        )
    if record.published_at > checked_at:
        raise DartDocumentError(
            "OpenDART index record publication date is later than checked_at"
        )
    index_hash = _index_record_hash(record)
    url = build_opendart_document_url(rcept_no=rcept_no, api_key=api_key)
    payload = fetch_bytes(url)
    if not isinstance(payload, bytes):
        raise DartDocumentFetchError("OpenDART binary transport must return bytes")
    return parse_opendart_original_document_archive(
        payload,
        rcept_no=rcept_no,
        checked_at=checked_at,
        published_at=record.published_at,
        index_record_hash=index_hash,
        source_ref=opendart_document_source_ref(rcept_no),
        policy=policy,
    )


def _receipt_number_from_index_record(record: DocumentIndexRecord) -> str:
    locator = (record.locator or "").strip()
    if locator:
        _validate_rcept_no(locator)
        return locator
    match = re.fullmatch(r"DART_(\d{14})", record.document_id)
    if not match:
        raise DartDocumentError(
            "OpenDART index record requires a 14-digit receipt number locator"
        )
    return match.group(1)


def _index_record_hash(record: DocumentIndexRecord) -> str:
    return stable_hash(
        {
            "source_id": record.source_id,
            "document_id": record.document_id,
            "title": record.title,
            "published_at": (
                record.published_at.isoformat() if record.published_at else None
            ),
            "url": record.url,
            "document_class": record.document_class,
            "period": record.period,
            "locator": record.locator,
            "content_fingerprint": record.content_fingerprint,
        }
    )


def _validate_members_against_retained_archive(
    document: DartOriginalFilingDocument,
) -> None:
    expected = {member.path: member for member in document.members}
    seen: set[str] = set()
    try:
        archive = ZipFile(BytesIO(document.archive_bytes))
    except BadZipFile as exc:
        raise DartDocumentError(
            "DART retained archive is not a valid ZIP"
        ) from exc
    with archive:
        for info in (item for item in archive.infolist() if not item.is_dir()):
            try:
                path = _safe_member_path(info.filename)
            except DartDocumentFetchError as exc:
                raise DartDocumentError(
                    f"DART retained archive contains unsafe member: {info.filename!r}"
                ) from exc
            if path in seen:
                raise DartDocumentError(
                    f"DART retained archive contains duplicate member path: {path}"
                )
            seen.add(path)
            member = expected.get(path)
            if member is None:
                raise DartDocumentError(
                    f"DART retained archive contains unmanifested member: {path}"
                )
            try:
                raw = archive.read(info)
            except (BadZipFile, NotImplementedError, RuntimeError) as exc:
                raise DartDocumentError(
                    f"DART retained archive member cannot be read: {path}"
                ) from exc
            if member.size_bytes != info.file_size:
                raise DartDocumentError(
                    f"DART retained member size mismatch: {path}"
                )
            if member.compressed_size_bytes != info.compress_size:
                raise DartDocumentError(
                    f"DART retained member compressed-size mismatch: {path}"
                )
            if member.content_hash != sha256(raw).hexdigest():
                raise DartDocumentError(
                    f"DART retained member content hash mismatch: {path}"
                )
            extension = PurePosixPath(path).suffix.lower()
            if member.media_type != _media_type(extension):
                raise DartDocumentError(
                    f"DART retained member media-type mismatch: {path}"
                )
            if extension in _TEXT_EXTENSIONS:
                try:
                    encoding = _detect_text_encoding(raw)
                    text = raw.decode(encoding, errors="strict")
                except (DartDocumentFetchError, UnicodeDecodeError, LookupError) as exc:
                    raise DartDocumentError(
                        f"DART retained text member cannot be reproduced: {path}"
                    ) from exc
                if member.text_encoding != encoding or member.text != text:
                    raise DartDocumentError(
                        f"DART retained decoded text mismatch: {path}"
                    )
            elif member.text is not None or member.text_encoding is not None:
                raise DartDocumentError(
                    f"DART retained binary member has unexpected decoded text: {path}"
                )
    missing = set(expected) - seen
    if missing:
        raise DartDocumentError(
            "DART retained archive is missing manifested members: "
            + ", ".join(sorted(missing))
        )


def _sanitize_source_ref(source_ref: str, *, expected_rcept_no: str) -> str:
    if not source_ref:
        raise DartDocumentError("DART original filing source_ref is required")
    parts = urlsplit(source_ref)
    query = parse_qsl(parts.query, keep_blank_values=True)
    cleaned: list[tuple[str, str]] = []
    receipt_values: list[str] = []
    for key, value in query:
        lower = key.lower()
        if lower in _SENSITIVE_QUERY_KEYS:
            continue
        if lower == "rcept_no":
            receipt_values.append(value)
        cleaned.append((key, value))
    if receipt_values and any(value != expected_rcept_no for value in receipt_values):
        raise DartDocumentError(
            "DART source_ref receipt number does not match requested filing"
        )
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(cleaned), parts.fragment)
    )


def _validate_rcept_no(rcept_no: str) -> None:
    if not re.fullmatch(r"\d{14}", rcept_no):
        raise DartDocumentError("OpenDART rcept_no must be exactly 14 digits")


def _safe_member_path(filename: str) -> str:
    raw = filename.replace("\\", "/").strip()
    path = PurePosixPath(raw)
    if (
        not raw
        or raw.startswith("/")
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise DartDocumentFetchError(
            f"unsafe OpenDART ZIP member path: {filename!r}"
        )
    return path.as_posix()


def _detect_text_encoding(raw: bytes) -> str:
    if raw.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    if raw.startswith(b"\xff\xfe"):
        return "utf-16-le"
    if raw.startswith(b"\xfe\xff"):
        return "utf-16-be"
    prefix = raw[:256].decode("ascii", errors="ignore")
    match = re.search(
        r"encoding\s*=\s*['\"](?P<encoding>[A-Za-z0-9._-]+)['\"]",
        prefix,
        flags=re.I,
    )
    if match:
        encoding = match.group("encoding").lower().replace("_", "-")
        aliases = {
            "ks-c-5601-1987": "cp949",
            "euc-kr": "euc-kr",
            "utf-8": "utf-8",
            "utf8": "utf-8",
            "utf-16": "utf-16",
        }
        resolved = aliases.get(encoding, encoding)
        try:
            codecs.lookup(resolved)
        except LookupError as exc:
            raise DartDocumentFetchError(
                f"OpenDART text member declares unsupported encoding: {encoding}"
            ) from exc
        return resolved
    try:
        raw.decode("utf-8", errors="strict")
        return "utf-8"
    except UnicodeDecodeError:
        return "cp949"


def _media_type(extension: str) -> str:
    return {
        ".xml": "application/xml",
        ".html": "text/html",
        ".htm": "text/html",
        ".xhtml": "application/xhtml+xml",
        ".txt": "text/plain",
    }.get(extension, "application/octet-stream")


def _parse_error_payload(payload: bytes) -> tuple[str | None, str | None]:
    for encoding in ("utf-8", "cp949", "euc-kr"):
        try:
            text = payload.decode(encoding, errors="strict")
            break
        except UnicodeDecodeError:
            text = ""
    if not text.strip():
        return None, None
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return None, None
    status = root.findtext(".//status")
    message = root.findtext(".//message")
    return (
        status.strip() if status else None,
        message.strip() if message else None,
    )

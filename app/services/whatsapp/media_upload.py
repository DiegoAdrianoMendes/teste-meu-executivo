"""WhatsApp Cloud API upload policy derived from Meta's supported media matrix."""

from __future__ import annotations

import codecs
import re
import warnings
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO
from xml.etree import ElementTree

from PIL import Image, UnidentifiedImageError


class WhatsAppMediaUploadError(ValueError):
    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class WhatsAppMediaPolicy:
    media_type: str
    content_type: str
    max_size_bytes: int


MIB = 1024 * 1024

_SUPPORTED_MEDIA: dict[str, tuple[str, int]] = {
    "image/jpeg": ("image", 5 * MIB),
    "image/png": ("image", 5 * MIB),
    "audio/aac": ("audio", 16 * MIB),
    "audio/amr": ("audio", 16 * MIB),
    "audio/mpeg": ("audio", 16 * MIB),
    "audio/mp4": ("audio", 16 * MIB),
    "audio/ogg": ("audio", 16 * MIB),
    "video/mp4": ("video", 16 * MIB),
    "video/3gpp": ("video", 16 * MIB),
    "text/plain": ("document", 100 * MIB),
    "application/pdf": ("document", 100 * MIB),
    "application/msword": ("document", 100 * MIB),
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": (
        "document",
        100 * MIB,
    ),
    "application/vnd.ms-excel": ("document", 100 * MIB),
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": (
        "document",
        100 * MIB,
    ),
    "application/vnd.ms-powerpoint": ("document", 100 * MIB),
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": (
        "document",
        100 * MIB,
    ),
}

_ALLOWED_EXTENSIONS: dict[str, frozenset[str]] = {
    "image/jpeg": frozenset({".jpg", ".jpeg"}),
    "image/png": frozenset({".png"}),
    "audio/aac": frozenset({".aac"}),
    "audio/amr": frozenset({".amr"}),
    "audio/mpeg": frozenset({".mp3"}),
    "audio/mp4": frozenset({".m4a", ".mp4"}),
    "audio/ogg": frozenset({".ogg", ".opus"}),
    "video/mp4": frozenset({".mp4", ".m4v"}),
    "video/3gpp": frozenset({".3gp", ".3gpp"}),
    "text/plain": frozenset({".txt"}),
    "application/pdf": frozenset({".pdf"}),
    "application/msword": frozenset({".doc"}),
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": (
        frozenset({".docx"})
    ),
    "application/vnd.ms-excel": frozenset({".xls"}),
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": (
        frozenset({".xlsx"})
    ),
    "application/vnd.ms-powerpoint": frozenset({".ppt"}),
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": (
        frozenset({".pptx"})
    ),
}

_OOXML_MAIN_CONTENT_TYPES = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
    ),
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"
    ),
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"
    ),
}

_OLE_CONTENT_TYPES = frozenset(
    {
        "application/msword",
        "application/vnd.ms-excel",
        "application/vnd.ms-powerpoint",
    }
)
_OLE_SIGNATURE = bytes.fromhex("d0cf11e0a1b11ae1")
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_PNG_END = b"\x00\x00\x00\x00IEND\xaeB\x60\x82"
_MAX_INSPECT_BYTES = MIB
_MAX_OOXML_EXPANDED_BYTES = 128 * MIB
_MAX_OOXML_ENTRY_BYTES = 64 * MIB
_MAX_OOXML_MANIFEST_BYTES = 2 * MIB
_MAX_OOXML_COMPRESSION_RATIO = 100
_MAX_OOXML_ENTRIES = 5_000
_ISO_CONTAINER_BOXES = frozenset(
    {b"moov", b"trak", b"mdia", b"minf", b"stbl", b"edts", b"dinf", b"udta", b"meta"}
)


def resolve_media_policy(content_type: str | None) -> WhatsAppMediaPolicy:
    normalized = str(content_type or "").split(";", 1)[0].strip().lower()
    policy = _SUPPORTED_MEDIA.get(normalized)
    if policy is None:
        raise WhatsAppMediaUploadError(
            "O formato do arquivo não é aceito pela API oficial do WhatsApp.",
            code="provider_media_content_type_unsupported",
        )
    media_type, max_size_bytes = policy
    return WhatsAppMediaPolicy(
        media_type=media_type,
        content_type=normalized,
        max_size_bytes=max_size_bytes,
    )


def validate_media_size(size_bytes: int, policy: WhatsAppMediaPolicy) -> None:
    if size_bytes <= 0:
        raise WhatsAppMediaUploadError(
            "O arquivo enviado está vazio.",
            code="provider_media_empty",
        )
    if size_bytes > policy.max_size_bytes:
        limit_mb = policy.max_size_bytes // MIB
        raise WhatsAppMediaUploadError(
            f"O arquivo excede o limite oficial de {limit_mb} MB para este formato.",
            code="provider_media_too_large",
        )


def normalized_media_extension(filename: str) -> str:
    return Path(str(filename or "")).suffix.casefold()


def _invalid_content(message: str) -> WhatsAppMediaUploadError:
    return WhatsAppMediaUploadError(
        message,
        code="provider_media_content_invalid",
    )


def _file_size(file_content: BinaryIO) -> int:
    file_content.seek(0, 2)
    size = file_content.tell()
    file_content.seek(0)
    return size


def _read_edges(file_content: BinaryIO, size: int) -> tuple[bytes, bytes]:
    file_content.seek(0)
    prefix = file_content.read(min(size, _MAX_INSPECT_BYTES))
    file_content.seek(max(0, size - _MAX_INSPECT_BYTES))
    tail = file_content.read(min(size, _MAX_INSPECT_BYTES))
    file_content.seek(0)
    return prefix, tail


def _validate_image(
    file_content: BinaryIO,
    *,
    content_type: str,
    prefix: bytes,
    tail: bytes,
) -> None:
    expected_format = "JPEG" if content_type == "image/jpeg" else "PNG"
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(file_content) as image:
                if image.format != expected_format:
                    raise _invalid_content(
                        "A assinatura da imagem não corresponde ao tipo declarado."
                    )
                image.verify()
    except WhatsAppMediaUploadError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise _invalid_content("A imagem excede os limites seguros de processamento.") from exc
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as exc:
        raise _invalid_content("A imagem está inválida ou corrompida.") from exc

    if content_type == "image/png":
        if not prefix.startswith(_PNG_SIGNATURE) or not tail.endswith(_PNG_END):
            raise _invalid_content("O contêiner PNG possui dados inválidos ou anexados.")
    elif not prefix.startswith(b"\xff\xd8\xff") or not tail.endswith(b"\xff\xd9"):
        raise _invalid_content("O contêiner JPEG possui dados inválidos ou anexados.")


def _validate_pdf(prefix: bytes, tail: bytes) -> None:
    if re.match(br"%PDF-(?:1\.[0-9]|2\.0)(?:\r\n|\r|\n)", prefix[:16]) is None:
        raise _invalid_content("O arquivo não possui uma assinatura PDF válida.")
    if re.search(br"\d+\s+\d+\s+obj\b", prefix) is None or b"endobj" not in prefix:
        raise _invalid_content("O PDF não possui uma estrutura de objetos válida.")
    normalized_tail = tail.rstrip(b"\x00\t\n\x0c\r ")
    if re.search(br"startxref\s+\d+\s+%%EOF$", normalized_tail) is None:
        raise _invalid_content("O PDF está incompleto ou contém dados após o encerramento.")


def _zip_has_exact_ending(file_content: BinaryIO, size: int) -> bool:
    tail_size = min(size, 65_557)
    file_content.seek(size - tail_size)
    tail = file_content.read(tail_size)
    marker = tail.rfind(b"PK\x05\x06")
    if marker < 0 or marker + 22 > len(tail):
        return False
    comment_length = int.from_bytes(tail[marker + 20 : marker + 22], "little")
    return marker + 22 + comment_length == len(tail)


def _validate_ooxml(
    file_content: BinaryIO,
    *,
    content_type: str,
    prefix: bytes,
    size: int,
) -> None:
    if not prefix.startswith(b"PK\x03\x04") or not _zip_has_exact_ending(
        file_content, size
    ):
        raise _invalid_content("O documento Office não possui um contêiner ZIP íntegro.")
    file_content.seek(0)
    try:
        with zipfile.ZipFile(file_content) as archive:
            infos = archive.infolist()
            if not infos or len(infos) > _MAX_OOXML_ENTRIES:
                raise _invalid_content("O documento Office possui uma estrutura insegura.")
            expanded_size = 0
            seen_paths: set[str] = set()
            manifest_info = None
            for info in infos:
                path = PurePosixPath(info.filename.replace("\\", "/"))
                if info.filename.startswith(("/", "\\")) or ".." in path.parts:
                    raise _invalid_content("O documento Office contém caminhos inseguros.")
                normalized_path = path.as_posix()
                if normalized_path in seen_paths:
                    raise _invalid_content("O documento Office contém entradas duplicadas.")
                seen_paths.add(normalized_path)
                if info.flag_bits & 0x1:
                    raise _invalid_content("O documento Office contém entradas criptografadas.")
                if info.file_size > _MAX_OOXML_ENTRY_BYTES:
                    raise _invalid_content("O documento Office contém uma entrada grande demais.")
                expanded_size += info.file_size
                if expanded_size > _MAX_OOXML_EXPANDED_BYTES:
                    raise _invalid_content("O documento Office expande além do limite seguro.")
                if info.file_size and info.compress_size == 0:
                    raise _invalid_content("O documento Office contém conteúdo suspeito.")
                if (
                    info.compress_size
                    and info.file_size / info.compress_size > _MAX_OOXML_COMPRESSION_RATIO
                ):
                    raise _invalid_content("O documento Office possui compressão suspeita.")
                if normalized_path == "[Content_Types].xml":
                    manifest_info = info
            if manifest_info is None or manifest_info.file_size > _MAX_OOXML_MANIFEST_BYTES:
                raise _invalid_content("O manifesto do documento Office excede o limite seguro.")
            with archive.open(manifest_info) as manifest:
                content_types = manifest.read(_MAX_OOXML_MANIFEST_BYTES + 1)
            if len(content_types) > _MAX_OOXML_MANIFEST_BYTES:
                raise _invalid_content("O manifesto do documento Office excede o limite seguro.")
    except WhatsAppMediaUploadError:
        raise
    except (KeyError, OSError, ValueError, zipfile.BadZipFile, RuntimeError) as exc:
        raise _invalid_content("O documento Office está inválido ou corrompido.") from exc

    if b"<!DOCTYPE" in content_types.upper() or b"<!ENTITY" in content_types.upper():
        raise _invalid_content("O manifesto do documento Office contém entidades proibidas.")
    try:
        root = ElementTree.fromstring(content_types)
    except ElementTree.ParseError as exc:
        raise _invalid_content("O manifesto do documento Office está inválido.") from exc
    declared_types = {
        str(element.attrib.get("ContentType") or "") for element in root.iter()
    }
    if sum(1 for _ in root.iter()) > 10_000:
        raise _invalid_content("O manifesto do documento Office é complexo demais.")
    if _OOXML_MAIN_CONTENT_TYPES[content_type] not in declared_types:
        raise _invalid_content(
            "O conteúdo do documento Office não corresponde ao tipo declarado."
        )


def _validate_text(file_content: BinaryIO) -> None:
    decoder = codecs.getincrementaldecoder("utf-8")("strict")
    file_content.seek(0)
    try:
        while chunk := file_content.read(MIB):
            if b"\x00" in chunk:
                raise _invalid_content("O arquivo de texto contém bytes binários.")
            decoder.decode(chunk)
        decoder.decode(b"", final=True)
    except UnicodeDecodeError as exc:
        raise _invalid_content("O arquivo de texto deve usar codificação UTF-8 válida.") from exc


def _mp3_frame_length(header: bytes) -> int | None:
    if len(header) < 4:
        return None
    value = int.from_bytes(header[:4], "big")
    if value & 0xFFE0_0000 != 0xFFE0_0000:
        return None
    version_id = (value >> 19) & 0x3
    layer_id = (value >> 17) & 0x3
    bitrate_index = (value >> 12) & 0xF
    sample_rate_index = (value >> 10) & 0x3
    padding = (value >> 9) & 0x1
    if version_id == 0x1 or layer_id != 0x1 or bitrate_index in {0, 0xF}:
        return None
    if sample_rate_index == 0x3:
        return None
    bitrate_table = (
        (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320)
        if version_id == 0x3
        else (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160)
    )
    sample_rates = {
        0x3: (44_100, 48_000, 32_000),
        0x2: (22_050, 24_000, 16_000),
        0x0: (11_025, 12_000, 8_000),
    }
    coefficient = 144 if version_id == 0x3 else 72
    return (
        coefficient * bitrate_table[bitrate_index] * 1_000
        // sample_rates[version_id][sample_rate_index]
        + padding
    )


def _validate_mp3(prefix: bytes) -> bool:
    offset = 0
    if prefix.startswith(b"ID3"):
        if len(prefix) < 10 or prefix[3] not in {2, 3, 4}:
            return False
        size_bytes = prefix[6:10]
        if any(value & 0x80 for value in size_bytes):
            return False
        tag_size = sum(value << shift for value, shift in zip(size_bytes, (21, 14, 7, 0)))
        offset = 10 + tag_size + (10 if prefix[5] & 0x10 else 0)
    first_length = _mp3_frame_length(prefix[offset : offset + 4])
    if first_length is None or offset + first_length + 4 > len(prefix):
        return False
    second_offset = offset + first_length
    second_length = _mp3_frame_length(prefix[second_offset : second_offset + 4])
    return second_length is not None and second_offset + second_length <= len(prefix)


def _adts_frame_length(data: bytes, offset: int) -> int | None:
    if offset + 7 > len(data):
        return None
    header = data[offset : offset + 7]
    if header[0] != 0xFF or header[1] & 0xF6 != 0xF0:
        return None
    if (header[2] >> 2) & 0xF == 0xF:
        return None
    frame_length = ((header[3] & 0x3) << 11) | (header[4] << 3) | (header[5] >> 5)
    header_length = 7 if header[1] & 0x1 else 9
    return frame_length if frame_length >= header_length else None


def _validate_aac(prefix: bytes) -> bool:
    first_length = _adts_frame_length(prefix, 0)
    if first_length is None or first_length + 7 > len(prefix):
        return False
    second_length = _adts_frame_length(prefix, first_length)
    return second_length is not None and first_length + second_length <= len(prefix)


def _validate_amr(prefix: bytes) -> bool:
    wide_band = prefix.startswith(b"#!AMR-WB\n")
    signature = b"#!AMR-WB\n" if wide_band else b"#!AMR\n"
    if not prefix.startswith(signature):
        return False
    frame_sizes = (
        (18, 24, 33, 37, 41, 47, 51, 59, 61, 6)
        if wide_band
        else (13, 14, 16, 18, 20, 21, 27, 32, 6)
    )
    offset = len(signature)
    frames = 0
    while frames < 2 and offset < len(prefix):
        frame_type = (prefix[offset] >> 3) & 0xF
        if frame_type >= len(frame_sizes):
            return False
        frame_size = 1 + frame_sizes[frame_type]
        if offset + frame_size > len(prefix):
            return False
        offset += frame_size
        frames += 1
    return frames >= 2


def _validate_ogg_opus(file_content: BinaryIO, size: int) -> bool:
    offset = 0
    pages = 0
    stream_serial: bytes | None = None
    expected_sequence = 0
    while offset < size and pages < 4_096:
        file_content.seek(offset)
        header = file_content.read(27)
        if len(header) != 27 or header[:4] != b"OggS" or header[4] != 0:
            return False
        segment_count = header[26]
        segments = file_content.read(segment_count)
        if len(segments) != segment_count:
            return False
        payload_length = sum(segments)
        payload = file_content.read(payload_length)
        if len(payload) != payload_length:
            return False
        if stream_serial is None:
            stream_serial = header[14:18]
            expected_sequence = int.from_bytes(header[18:22], "little")
            if not payload.startswith(b"OpusHead") or len(payload) < 19:
                return False
            if payload[8] != 1 or payload[9] == 0:
                return False
        elif header[14:18] != stream_serial:
            return False
        sequence = int.from_bytes(header[18:22], "little")
        if sequence != expected_sequence:
            return False
        expected_sequence += 1
        pages += 1
        offset += 27 + segment_count + payload_length
    return pages >= 2 and offset == size


def _scan_iso_boxes(
    file_content: BinaryIO,
    *,
    start: int,
    end: int,
    depth: int,
    budget: list[int],
    handlers: set[bytes],
    top_level: list[bytes],
    brands: set[bytes],
) -> bool:
    if depth > 8:
        return False
    offset = start
    while offset < end:
        budget[0] += 1
        if budget[0] > 10_000 or end - offset < 8:
            return False
        file_content.seek(offset)
        header = file_content.read(8)
        box_size = int.from_bytes(header[:4], "big")
        box_type = header[4:8]
        header_size = 8
        if box_size == 1:
            extended = file_content.read(8)
            if len(extended) != 8:
                return False
            box_size = int.from_bytes(extended, "big")
            header_size = 16
        elif box_size == 0:
            box_size = end - offset
        if box_size < header_size or offset + box_size > end:
            return False
        payload_start = offset + header_size
        payload_end = offset + box_size
        if depth == 0:
            top_level.append(box_type)
        if box_type == b"ftyp":
            payload_size = payload_end - payload_start
            if depth != 0 or payload_size < 8 or payload_size > 256 or payload_size % 4:
                return False
            file_content.seek(payload_start)
            payload = file_content.read(payload_size)
            brands.update(payload[index : index + 4] for index in range(0, len(payload), 4))
        elif box_type == b"hdlr":
            if payload_end - payload_start < 12:
                return False
            file_content.seek(payload_start)
            handler = file_content.read(12)[8:12]
            handlers.add(handler)
        if box_type in _ISO_CONTAINER_BOXES:
            child_start = payload_start + (4 if box_type == b"meta" else 0)
            if child_start > payload_end or not _scan_iso_boxes(
                file_content,
                start=child_start,
                end=payload_end,
                depth=depth + 1,
                budget=budget,
                handlers=handlers,
                top_level=top_level,
                brands=brands,
            ):
                return False
        offset = payload_end
    return offset == end


def _validate_iso_media(file_content: BinaryIO, size: int, content_type: str) -> bool:
    handlers: set[bytes] = set()
    top_level: list[bytes] = []
    brands: set[bytes] = set()
    if not _scan_iso_boxes(
        file_content,
        start=0,
        end=size,
        depth=0,
        budget=[0],
        handlers=handlers,
        top_level=top_level,
        brands=brands,
    ):
        return False
    if not top_level or top_level[0] != b"ftyp":
        return False
    if not {b"ftyp", b"moov", b"mdat"}.issubset(top_level):
        return False
    if content_type == "video/3gpp":
        return b"vide" in handlers and any(brand.startswith(b"3g") for brand in brands)
    if content_type == "video/mp4":
        return b"vide" in handlers
    return b"soun" in handlers and b"vide" not in handlers


def _validate_audio_or_video(
    file_content: BinaryIO,
    *,
    content_type: str,
    prefix: bytes,
    size: int,
) -> None:
    if content_type == "audio/aac":
        valid = _validate_aac(prefix)
    elif content_type == "audio/amr":
        valid = _validate_amr(prefix)
    elif content_type == "audio/mpeg":
        valid = _validate_mp3(prefix)
    elif content_type == "audio/ogg":
        valid = _validate_ogg_opus(file_content, size)
    else:
        valid = _validate_iso_media(file_content, size, content_type)
    if not valid:
        raise _invalid_content(
            "O contêiner do áudio ou vídeo não corresponde ao tipo declarado."
        )


def validate_media_content(
    file_content: BinaryIO,
    *,
    filename: str,
    policy: WhatsAppMediaPolicy,
) -> None:
    """Verify extension, magic bytes and bounded container integrity before upload."""
    extension = normalized_media_extension(filename)
    allowed_extensions = _ALLOWED_EXTENSIONS[policy.content_type]
    if extension not in allowed_extensions:
        expected = ", ".join(sorted(allowed_extensions))
        raise WhatsAppMediaUploadError(
            f"A extensão do arquivo deve ser compatível com {expected}.",
            code="provider_media_extension_mismatch",
        )

    try:
        size = _file_size(file_content)
        validate_media_size(size, policy)
        prefix, tail = _read_edges(file_content, size)
        if policy.content_type in {"image/jpeg", "image/png"}:
            _validate_image(
                file_content,
                content_type=policy.content_type,
                prefix=prefix,
                tail=tail,
            )
        elif policy.content_type == "application/pdf":
            _validate_pdf(prefix, tail)
        elif policy.content_type in _OOXML_MAIN_CONTENT_TYPES:
            _validate_ooxml(
                file_content,
                content_type=policy.content_type,
                prefix=prefix,
                size=size,
            )
        elif policy.content_type in _OLE_CONTENT_TYPES:
            if not prefix.startswith(_OLE_SIGNATURE):
                raise _invalid_content(
                    "O documento Office legado não possui uma assinatura OLE válida."
                )
        elif policy.content_type == "text/plain":
            _validate_text(file_content)
        else:
            _validate_audio_or_video(
                file_content,
                content_type=policy.content_type,
                prefix=prefix,
                size=size,
            )
    finally:
        file_content.seek(0)
